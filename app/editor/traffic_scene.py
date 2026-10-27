"""Animated, lane-aware schematic for a signalized intersection.

Vehicles follow complete turn-specific paths across the junction; pedestrians traverse
the entire marked crossing. This remains a visual teaching model, not calibrated
microsimulation or a substitute for a surveyed intersection plan.
"""
from math import hypot
import math
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath
from PySide6.QtWidgets import QWidget, QMenu
from app.simulation.detailed import resolve_phase_movements, MovementDemand, vehicle_movements_conflict
from app.signs import SIGN_CATALOG, movement_allowed_by_signs


class TrafficScene(QWidget):
    APPROACHES = ("Север", "Северо-восток", "Восток", "Юго-восток", "Юг", "Юго-запад", "Запад", "Северо-запад")
    TURNS = ("Прямо", "Налево", "Направо")
    TURN_GLYPHS = {
        "Север": {"Прямо": "↓", "Налево": "↘", "Направо": "↙"},
        "Юг": {"Прямо": "↑", "Налево": "↖", "Направо": "↗"},
        "Восток": {"Прямо": "←", "Налево": "↙", "Направо": "↖"},
        "Запад": {"Прямо": "→", "Налево": "↗", "Направо": "↘"},
    }
    COLORS = {
        "green": QColor("#22c55e"), "flash": QColor("#86efac"),
        "yellow": QColor("#facc15"), "red": QColor("#ef4444"),
        "intergreen": QColor("#ef4444"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(620, 480)
        self.running = False
        self.speed_multiplier = 1
        self.sim_time = 0.0
        self.phase_elapsed = 0.0
        self.phase_index = 0
        self.phases = []
        self.movements = []
        self.pedestrians = []
        self.road_signs = []
        self.lane_counts = {name: 2 for name in ("Север", "Восток", "Юг", "Запад")}
        self.lane_turns = {}
        self.vehicles = []
        self.walkers = []
        self.spawn_clock = {}
        self.ped_spawn_clock = {}
        self._geometry = None
        self.playback_events = []
        self.event_cursor = 0
        self.replay_mode = False
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._advance)

    def set_speed(self, multiplier):
        self.speed_multiplier = 2 if int(multiplier) >= 2 else 1
        self.update()

    def configure(self, phases, movements, pedestrians, cycle_seconds, lane_counts=None, lane_turns=None, result=None, signs=None):
        self.pause()
        self.phases = list(phases)
        self.movements = list(movements)
        self.pedestrians = list(pedestrians)
        if signs is not None:
            self.road_signs = [dict(sign) for sign in signs]
        if lane_counts:
            self.lane_counts = dict(lane_counts)
        if lane_turns is not None:
            self.lane_turns = dict(lane_turns)
        self.sim_time = 0.0
        self.phase_elapsed = 0.0
        self.phase_index = 0
        self.vehicles.clear()
        self.walkers.clear()
        self.spawn_clock.clear()
        self.ped_spawn_clock.clear()
        self.playback_events = sorted((getattr(result, "events", None) or []), key=lambda e: (e["time"], 0 if e["kind"].endswith("arrival") else 1))
        self.event_cursor = 0
        self.pending_vehicle_services = []
        self.replay_mode = result is not None
        self.update()

    def start(self):
        if self.phases:
            self.running = True
            self.timer.start()
            self.update()

    def pause(self):
        self.running = False
        self.timer.stop()
        self.update()

    def reset(self):
        self.pause()
        self.sim_time = 0.0
        self.phase_elapsed = 0.0
        self.phase_index = 0
        self.vehicles.clear()
        self.walkers.clear()
        self.spawn_clock.clear()
        self.ped_spawn_clock.clear()
        self.event_cursor = 0
        self.pending_vehicle_services = []
        self.update()

    def _advance(self):
        # Replay the exact arrivals/service events used by the report. 10x playback
        # keeps a long scenario reviewable; the UI toggle changes it to 20x.
        dt = 0.5 * self.speed_multiplier
        self.sim_time += dt
        self.phase_elapsed += dt
        if self.phases:
            while self.phase_elapsed >= max(1, self.phases[self.phase_index].duration_seconds):
                self.phase_elapsed -= max(1, self.phases[self.phase_index].duration_seconds)
                self.phase_index = (self.phase_index + 1) % len(self.phases)
        if self.replay_mode:
            self._consume_replay_events()
        else:
            stage, active = self._stage(), self._active_ids()
            self._spawn_eligible(stage, active, dt)
        stage, active = self._stage(), self._active_ids()
        self._release_pending_vehicle_services(stage, active)
        # Re-pack waiting cars as the queue discharges; a follower advances
        # toward the stop line when the car ahead enters the junction.
        by_movement = {}
        for vehicle in self.vehicles:
            by_movement.setdefault(vehicle["key"], []).append(vehicle)
        for group in by_movement.values():
            waiting = [v for v in group if (not v.get("released", False) or
                       v["distance"] <= v["stop_distance"])]
            for index, vehicle in enumerate(waiting):
                vehicle["stop_distance"] = vehicle.get("base_stop_distance", vehicle["stop_distance"]) - 24.0 * index
        for vehicle in self.vehicles:
            # Fixed-step style motion: accelerate smoothly, brake toward the stop line,
            # and never advance past it until a release/service event is received.
            target_speed = 46.0
            remaining = vehicle["stop_distance"] - vehicle["distance"]
            released = vehicle.get("released", False)
            # A permissive left turn may receive a release event while the crossing
            # conflict area is still occupied. Hold it at the stop line, then continue
            # automatically once the conflicting vehicle clears; never brake mid-turn.
            blocked_by_conflict = False
            if released and vehicle["distance"] <= vehicle["stop_distance"] + 1.0:
                try:
                    approach, lane, turn = vehicle["key"].split(":", 2)
                    current = MovementDemand(approach, int(lane), turn, 0)
                    for other in self.vehicles:
                        if other is vehicle or other["distance"] < other["stop_distance"] + 1.0:
                            continue
                        if other["distance"] >= other["length"] - 2.0:
                            continue
                        other_approach, other_lane, other_turn = other["key"].split(":", 2)
                        opposing = MovementDemand(other_approach, int(other_lane), other_turn, 0)
                        if vehicle_movements_conflict(current, opposing):
                            blocked_by_conflict = True
                            break
                except (ValueError, TypeError):
                    blocked_by_conflict = False
            if blocked_by_conflict or (not released and remaining <= max(5.0, vehicle["speed"] * 0.9)):
                target_speed = 0.0
            acceleration = 34.0 if target_speed > vehicle["speed"] else 70.0
            delta = max(-acceleration * dt, min(acceleration * dt, target_speed - vehicle["speed"]))
            vehicle["speed"] = max(0.0, vehicle["speed"] + delta)
            proposed = vehicle["distance"] + vehicle["speed"] * dt
            if not released:
                vehicle["distance"] = min(proposed, vehicle["stop_distance"])
                if vehicle["distance"] >= vehicle["stop_distance"] - 0.5:
                    vehicle["speed"] = 0.0
            else:
                vehicle["distance"] = proposed
        self.vehicles = [v for v in self.vehicles if v["distance"] < v["length"]]
        for walker in self.walkers:
            if walker.get("released", True):
                walker["distance"] += walker["speed"] * dt
        self.walkers = [v for v in self.walkers if v["distance"] < v["length"]]
        self.update()

    def _consume_replay_events(self):
        while self.event_cursor < len(self.playback_events) and self.playback_events[self.event_cursor]["time"] <= self.sim_time:
            event = self.playback_events[self.event_cursor]
            key = event["movement_id"]
            if event["kind"] == "vehicle_arrival":
                demand = next((m for m in self.movements if m.movement_id == key), None)
                if demand is not None:
                    path, base_stop_distance = self._vehicle_path(demand.approach, demand.lane, demand.turn)
                    if path is None:
                        self.event_cursor += 1
                        continue
                    # Keep vehicles in a real queue: each follower starts and stops
                    # one vehicle length behind the preceding car in the same movement.
                    queued = sum(1 for v in self.vehicles if v["key"] == key
                                  and v["distance"] < v["length"]
                                  and (not v.get("released", False) or v["distance"] <= v["stop_distance"]))
                    queue_offset = 24.0 * queued
                    vehicle_type = self._vehicle_type_for_index(len(self.vehicles) + self.event_cursor)
                    self.vehicles.append({"key": key, "path": path, "distance": -queue_offset,
                                          "length": path.length(), "base_stop_distance": base_stop_distance,
                                          "stop_distance": base_stop_distance - queue_offset,
                                          "speed": 46.0, "turn": demand.turn, "vehicle_type": vehicle_type,
                                          "released": False})
            elif event["kind"] == "vehicle_served":
                # The aggregate model can serve a vehicle before the animation
                # reaches its stop line. Queue the event; release only on green.
                pending = getattr(self, "pending_vehicle_services", [])
                pending.append(key)
                self.pending_vehicle_services = pending
            elif event["kind"] == "pedestrian_arrival":
                demand = next((p for p in self.pedestrians if p.movement_id == key), None)
                if demand is not None:
                    path = self._pedestrian_path(demand.crossing)
                    self.walkers.append({"key": key, "path": path, "distance": 0.0,
                                         "length": path.length(), "speed": 48.0,
                                         "style": self.event_cursor % 3, "released": False})
            elif event["kind"] == "pedestrian_served":
                walker = next((v for v in self.walkers if v["key"] == key and not v.get("released", False)), None)
                if walker is not None:
                    walker["released"] = True
            self.event_cursor += 1

    def _release_pending_vehicle_services(self, stage, active):
        """Release replay vehicles only when their movement has a permissive signal."""
        if stage not in ("green", "flash"):
            return
        pending = getattr(self, "pending_vehicle_services", [])
        remaining = []
        for key in pending:
            if key not in active:
                remaining.append(key)
                continue
            vehicle = next((v for v in self.vehicles
                            if v["key"] == key and not v.get("released", False)
                            and v["distance"] >= v["stop_distance"] - 1.0), None)
            if vehicle is None:
                remaining.append(key)
            else:
                vehicle["released"] = True
        self.pending_vehicle_services = remaining

    @staticmethod
    def _vehicle_type_for_index(index):
        # A varied but repeatable vehicle mix for the current aggregate-demand model.
        mix = ("car", "car", "car", "bus", "truck", "car", "articulated_bus",
               "car", "lorry", "dump_truck", "car", "truck")
        return mix[index % len(mix)]

    def _stage(self):
        if not self.phases:
            return "red"
        phase = self.phases[self.phase_index]
        t = self.phase_elapsed
        if t < phase.green_seconds:
            return "green"
        t -= phase.green_seconds
        if t < phase.flashing_green_seconds:
            return "flash"
        t -= phase.flashing_green_seconds
        if t < phase.yellow_seconds:
            return "yellow"
        t -= phase.yellow_seconds
        if t < phase.red_seconds:
            return "red"
        return "intergreen"

    def _active_ids(self):
        if not self.phases:
            return set()
        all_ids = [m.movement_id for m in self.movements] + [p.movement_id for p in self.pedestrians]
        active = set()
        for token in self.phases[self.phase_index].movements:
            if token.endswith(":*"):
                active.update(key for key in all_ids if key.startswith(token[:-1]))
            else:
                active.add(token)
        # Apply prohibitory/mandatory signs before resolving vehicle conflicts.
        movement_map = {m.movement_id: m for m in self.movements}
        active = {
            key for key in active
            if key not in movement_map or movement_allowed_by_signs(
                movement_map[key].approach, movement_map[key].turn, self.road_signs
            )
        }
        return resolve_phase_movements(active, self.movements, self.pedestrians, signs=self.road_signs)

    @staticmethod
    def _movement_state(movement_id, stage, active):
        if movement_id not in active:
            return "red"
        return stage if stage in ("green", "flash", "yellow") else "red"

    def _spawn_eligible(self, stage, active, dt):
        can_move = stage in ("green", "flash")
        if can_move:
            for demand in self.movements:
                key = demand.movement_id
                if key not in active:
                    continue
                rate = max(1, int(getattr(demand, "vehicles_per_hour", getattr(demand, "vehicles_per_hour", 0)) or 0))
                # MovementDemand stores its hourly demand as vehicles_per_hour in the current model.
                if hasattr(demand, "vehicles_per_hour"):
                    rate = max(0, int(demand.vehicles_per_hour))
                elif hasattr(demand, "volume_per_hour"):
                    rate = max(0, int(demand.volume_per_hour))
                else:
                    rate = max(0, int(getattr(demand, "demand_per_hour", 0)))
                if rate <= 0:
                    continue
                self.spawn_clock[key] = self.spawn_clock.get(key, 0.0) + dt
                interval = max(1.25, 3600.0 / rate)
                if self.spawn_clock[key] >= interval and not any(v["key"] == key and v["distance"] < 42 for v in self.vehicles):
                    path, stop_distance = self._vehicle_path(demand.approach, demand.lane, demand.turn)
                    length = path.length() if path is not None else 0
                    if length > 0:
                        self.vehicles.append({"key": key, "path": path, "distance": 0.0,
                                              "length": length, "base_stop_distance": stop_distance,
                                              "stop_distance": stop_distance,
                                              "speed": 46.0, "turn": demand.turn,
                                              "vehicle_type": self._vehicle_type_for_index(len(self.vehicles)),
                                              "released": False})
                        self.spawn_clock[key] = 0.0
        for pedestrian in self.pedestrians:
            key = pedestrian.movement_id
            state = self._movement_state(key, stage, active)
            if state not in ("green", "flash"):
                continue
            rate = max(0, int(getattr(pedestrian, "pedestrians_per_hour", 0)))
            if rate <= 0:
                continue
            self.ped_spawn_clock[key] = self.ped_spawn_clock.get(key, 0.0) + dt
            interval = max(0.8, 3600.0 / rate)
            if self.ped_spawn_clock[key] >= interval and not any(v["key"] == key and v["distance"] < 18 for v in self.walkers):
                path = self._pedestrian_path(pedestrian.crossing)
                self.walkers.append({"key": key, "path": path, "distance": 0.0,
                                     "length": path.length(), "speed": 48.0,
                                     "style": len(self.walkers) % 3, "released": True})
                self.ped_spawn_clock[key] = 0.0

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.RightButton:
            return super().mousePressEvent(event)
        g = self._layout()
        x, y = event.position().x(), event.position().y()
        if y < g["top"]:
            approach = "Север"
        elif y > g["bottom"]:
            approach = "Юг"
        elif x < g["left"]:
            approach = "Запад"
        elif x > g["right"]:
            approach = "Восток"
        else:
            return
        menu = QMenu(self)
        remove = menu.addAction(f"Убрать знаки с подхода «{approach}»")
        menu.addSeparator()
        actions = {}
        for code, (name, _group) in SIGN_CATALOG.items():
            actions[menu.addAction(f"{code} — {name}")] = code
        chosen = menu.exec(event.globalPosition().toPoint())
        if chosen is None:
            return
        if chosen == remove:
            self.road_signs = [sign for sign in self.road_signs if sign.get("approach") != approach]
        elif chosen in actions:
            code = actions[chosen]
            self.road_signs = [sign for sign in self.road_signs
                               if not (sign.get("approach") == approach and sign.get("code") == code)]
            self.road_signs.append({"approach": approach, "code": code, "name": SIGN_CATALOG[code][0]})
        self.update()

    def _layout(self):
        w, h = float(self.width()), float(self.height())
        road_w = min(w * 0.47, h * 0.58)
        cx, cy = w / 2, h / 2 + 5
        left, right = cx - road_w / 2, cx + road_w / 2
        top, bottom = cy - road_w / 2, cy + road_w / 2
        return {"w": w, "h": h, "cx": cx, "cy": cy, "road": road_w,
                "left": left, "right": right, "top": top, "bottom": bottom}

    def _approach_bearing(self, approach):
        from app.simulation.detailed import APPROACH_BEARINGS
        return APPROACH_BEARINGS.get(approach, 0)

    def _lane_offset(self, approach, lane, road_width):
        """One shared lane-centre offset for vehicles, stop lines and signal heads."""
        count = max(1, int(self.lane_counts.get(approach, 1)))
        lane = min(count, max(1, int(lane)))
        spacing = min(13.0, road_width * 0.035)
        return (lane - (count + 1) / 2) * spacing

    def _lane_center(self, approach, lane, g):
        bearing = math.radians(self._approach_bearing(approach))
        offset = self._lane_offset(approach, lane, g["road"])
        nx, ny = math.sin(bearing), -math.cos(bearing)
        tx, ty = math.cos(bearing), math.sin(bearing)
        radius = g["road"] * 0.56
        return QPointF(g["cx"] + nx * radius - tx * offset,
                       g["cy"] + ny * radius - ty * offset)

    def _vehicle_path(self, approach, lane, turn):
        """Build a smooth lane-centred path that remains inside the paved arms."""
        g = self._layout()
        bearing = self._approach_bearing(approach)
        angle = math.radians(bearing)
        nx, ny = math.sin(angle), -math.cos(angle)
        tx, ty = math.cos(angle), math.sin(angle)
        lane_offset = self._lane_offset(approach, lane, g["road"])
        road_radius = g["road"] * 0.43
        center = QPointF(g["cx"] + nx * road_radius - tx * lane_offset,
                         g["cy"] + ny * road_radius - ty * lane_offset)
        # Put the queue on the incoming arm, not in the junction.
        approach_margin = max(100.0, g["road"] * 0.92)
        stop_offset = g["road"] * 0.43
        start = QPointF(center.x() + nx * approach_margin, center.y() + ny * approach_margin)
        stop = QPointF(center.x() + nx * stop_offset, center.y() + ny * stop_offset)
        stop_distance = math.hypot(stop.x() - start.x(), stop.y() - start.y())

        turn_offsets = {"Прямо": 180, "Налево": 90, "Направо": -90}
        exit_deg = (bearing + turn_offsets.get(turn, 180)) % 360
        # Select only a configured arm matching the requested movement. Never
        # silently redirect traffic onto an unrelated arm at a T/multi-arm junction.
        arms = list(self.lane_counts)
        if not arms:
            return None, stop_distance
        def angle_delta(a, b):
            return abs((a - b + 180) % 360 - 180)
        exit_side = min(arms, key=lambda name: angle_delta(self._approach_bearing(name), exit_deg))
        if angle_delta(self._approach_bearing(exit_side), exit_deg) > 30:
            return None, stop_distance
        exit_deg = self._approach_bearing(exit_side)
        exit_angle = math.radians(exit_deg)
        ex, ey = math.sin(exit_angle), -math.cos(exit_angle)
        etx, ety = math.cos(exit_angle), math.sin(exit_angle)
        exit_count = max(1, self.lane_counts.get(exit_side, 1))
        exit_lane = min(exit_count, max(1, lane))
        exit_offset = self._lane_offset(exit_side, exit_lane, g["road"])
        # End the curve just beyond the visible road's end-cap, aligned to the
        # selected arm so the whole vehicle stays on the asphalt while turning.
        exit_radius = max(g["w"], g["h"]) * 0.66
        end = QPointF(g["cx"] + ex * exit_radius - etx * exit_offset,
                      g["cy"] + ey * exit_radius - ety * exit_offset)
        path = QPainterPath(start)
        path.lineTo(stop)
        # A pair of cubic curves gives a continuous tangent through the junction.
        entry = QPointF(g["cx"] + nx * road_radius - tx * lane_offset,
                        g["cy"] + ny * road_radius - ty * lane_offset)
        c1 = QPointF(entry.x() - nx * g["road"] * 0.08 - tx * lane_offset,
                     entry.y() - ny * g["road"] * 0.08 - ty * lane_offset)
        c2 = QPointF(g["cx"] + ex * g["road"] * 0.16 - etx * exit_offset,
                     g["cy"] + ey * g["road"] * 0.16 - ety * exit_offset)
        path.cubicTo(c1, c2, end)
        return path, stop_distance

    def _pedestrian_path(self, crossing):
        g = self._layout()
        from app.simulation.detailed import APPROACH_BEARINGS
        bearing = math.radians(APPROACH_BEARINGS.get(crossing, 0))
        nx, ny = math.cos(bearing), math.sin(bearing)
        tx, ty = -ny, nx
        radius = g["road"] * 0.63
        half_width = g["road"] * 0.42
        cx, cy = g["cx"], g["cy"]
        center_x, center_y = cx + nx * radius, cy + ny * radius
        path = QPainterPath(QPointF(center_x - tx * half_width, center_y - ty * half_width))
        path.lineTo(QPointF(center_x + tx * half_width, center_y + ty * half_width))
        return path

    def _point_at(self, item):
        path = item["path"]
        length = max(1.0, item["length"])
        distance = item["distance"]
        # Negative distances place queued vehicles upstream of the visible path start.
        if distance < 0:
            start = path.pointAtPercent(0.0)
            step = path.pointAtPercent(path.percentAtLength(min(1.0, length)))
            dx, dy = step.x() - start.x(), step.y() - start.y()
            norm = max(1.0, hypot(dx, dy))
            return QPointF(start.x() + dx / norm * distance, start.y() + dy / norm * distance)
        percent = path.percentAtLength(min(length, distance))
        return path.pointAtPercent(percent)

    @staticmethod
    def _draw_arrow(p, x, y, glyph, color=QColor("#f8fafc")):
        p.setPen(color)
        p.setFont(QFont("Segoe UI Symbol", 12, QFont.Weight.Bold))
        p.drawText(int(x - 9), int(y + 5), glyph)

    def _draw_signal_head(self, p, x, y, state, horizontal=False):
        p.save()
        p.setPen(QPen(QColor("#0f172a"), 1))
        p.setBrush(QColor("#111827"))
        if horizontal:
            p.drawRoundedRect(int(x - 22), int(y - 8), 44, 16, 5, 5)
            centers = [(x - 12, y), (x, y), (x + 12, y)]
        else:
            p.drawRoundedRect(int(x - 8), int(y - 22), 16, 44, 5, 5)
            centers = [(x, y - 12), (x, y), (x, y + 12)]
        active_index = {"red": 0, "yellow": 1, "green": 2, "flash": 2}.get(state, 0)
        colors = [QColor("#ef4444"), QColor("#facc15"), QColor("#22c55e")]
        for i, (lx, ly) in enumerate(centers):
            color = colors[i] if i == active_index else QColor("#3f4854")
            if i == 2 and state == "flash" and int(self.sim_time * 4) % 2:
                color = QColor("#3f4854")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color)
            p.drawEllipse(int(lx - 4), int(ly - 4), 8, 8)
        p.restore()

    @staticmethod
    def _draw_road_sign(p, x, y, code):
        """Paint a compact but recognizable Russian road-sign face with vector geometry."""
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(x, y)
        red = QColor("#d71920")
        blue = QColor("#0759a6")
        white = QColor("#ffffff")
        black = QColor("#15191f")
        p.setPen(QPen(QColor("#27313b"), 1))
        p.setBrush(white)

        def polygon(points, fill, outline=black, width=1.5):
            p.setPen(QPen(outline, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(fill)
            p.drawPolygon([QPointF(x + px, y + py) for px, py in points])

        def arrow(glyph, color=white, size=21):
            p.setPen(color)
            p.setFont(QFont("DejaVu Sans", size, QFont.Weight.Bold))
            p.drawText(int(x - 15), int(y - 10), 30, 23, Qt.AlignmentFlag.AlignCenter, glyph)

        if code == "2.1":
            polygon([(0,-19),(19,0),(0,19),(-19,0)], QColor("#f4cc21"), white, 3)
            polygon([(0,-15),(15,0),(0,15),(-15,0)], QColor("#f4cc21"), QColor("#b28a00"), 1)
        elif code == "2.4":
            polygon([(-22,-15),(22,-15),(0,22)], white, red, 5)
        elif code == "2.5":
            import math
            polygon([(19*math.cos(math.radians(22.5+i*45)),19*math.sin(math.radians(22.5+i*45))) for i in range(8)], red, white, 2)
            p.setPen(white); p.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            p.drawText(int(x-15), int(y-5), 30, 12, Qt.AlignmentFlag.AlignCenter, "STOP")
        elif code in ("2.6", "2.7"):
            polygon([(-21,-17),(21,-17),(21,17),(-21,17)], white if code=="2.6" else blue, white, 1)
            p.setPen(QPen(red if code=="2.6" else white, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(int(x-8),int(y+11),int(x-8),int(y-11))
            p.drawLine(int(x-8),int(y-11),int(x-13),int(y-5))
            p.drawLine(int(x-8),int(y-11),int(x-3),int(y-5))
            p.setPen(QPen(blue if code=="2.6" else white, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(int(x+8),int(y-11),int(x+8),int(y+11))
            p.drawLine(int(x+8),int(y+11),int(x+3),int(y+5))
            p.drawLine(int(x+8),int(y+11),int(x+13),int(y+5))
        elif code == "3.1":
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(red); p.drawEllipse(center, 20, 20)
            p.setBrush(white); p.drawRoundedRect(int(x-14),int(y-5),28,10,2,2)
        elif code in ("3.2","3.18.1","3.18.2","3.19"):
            p.setPen(QPen(red, 5)); p.setBrush(white); p.drawEllipse(center, 19, 19)
            if code == "3.2":
                p.setPen(QPen(red, 5)); p.drawLine(int(x-12),int(y+12),int(x+12),int(y-12))
            else:
                glyph = {"3.18.1":"→","3.18.2":"←","3.19":"↶"}[code]
                p.setPen(black); p.setFont(QFont("DejaVu Sans", 22, QFont.Weight.Bold))
                p.drawText(int(x-16),int(y-14),32,28,Qt.AlignmentFlag.AlignCenter,glyph)
                p.setPen(QPen(red, 4)); p.drawLine(int(x-14),int(y+14),int(x+14),int(y-14))
        elif code.startswith("4.1."):
            p.setPen(QPen(white, 1)); p.setBrush(blue); p.drawEllipse(center, 20, 20)
            glyph = {"4.1.1":"↑","4.1.2":"→","4.1.3":"←","4.1.4":"↗","4.1.5":"↖","4.1.6":"↔"}.get(code,"↑")
            arrow(glyph, white, 22)
        else:
            p.setPen(QPen(red, 4)); p.setBrush(white); p.drawEllipse(center, 19, 19)
            p.setPen(black); p.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            p.drawText(int(x-15),int(y-5),30,10,Qt.AlignmentFlag.AlignCenter,code)
        p.restore()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#eaf0e8"))
        g = self._layout()
        w, h, cx, cy, road = (g[k] for k in ("w", "h", "cx", "cy", "road"))
        left, right, top, bottom = (g[k] for k in ("left", "right", "top", "bottom"))
        # Header and intersection surface.
        p.setPen(QColor("#172033"))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        phase = self.phases[self.phase_index] if self.phases else None
        stage = self._stage()
        active = self._active_ids()
        stage_label = {"green": "ЗЕЛЁНЫЙ", "flash": "МИГАЮЩИЙ ЗЕЛЁНЫЙ", "yellow": "ЖЁЛТЫЙ",
                       "red": "КРАСНЫЙ", "intergreen": "ВСЕМ КРАСНЫЙ"}[stage]
        phase_label = phase.name if phase else "Фазы не заданы"
        p.drawText(10, 18, f"ФАЗА {self.phase_index + 1 if phase else 0}: {phase_label} · {stage_label}")
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(10, 35, f"Правостороннее движение · Время: {self.phase_elapsed:.1f} с · "
                            f"Скорость: {self.speed_multiplier}× · "
                            f"{'АНИМАЦИЯ' if self.running else 'ПАУЗА'}")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#3b434d"))
        p.setPen(QPen(QColor("#3b434d"), road, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.RoundJoin))
        from app.simulation.detailed import APPROACH_BEARINGS
        for arm in self.lane_counts:
            bearing = math.radians(APPROACH_BEARINGS.get(arm, 0))
            endpoint = QPointF(cx + math.sin(bearing) * max(w, h) * 0.72,
                               cy - math.cos(bearing) * max(w, h) * 0.72)
            road_path = QPainterPath(QPointF(cx, cy))
            road_path.lineTo(endpoint)
            p.drawPath(road_path)
        # Lane separators are drawn radially for every configured arm. This
        # keeps markings aligned on T, curved, five-arm and six-arm layouts.
        from app.simulation.detailed import APPROACH_BEARINGS
        p.setPen(QPen(QColor("#b9c2ca"), 1, Qt.PenStyle.DashLine))
        for approach, count_value in self.lane_counts.items():
            count = max(1, count_value)
            bearing = math.radians(APPROACH_BEARINGS.get(approach, 0))
            nx, ny = math.sin(bearing), -math.cos(bearing)
            tx, ty = math.cos(bearing), math.sin(bearing)
            inner = road * 0.47
            outer = max(w, h) * 0.72
            for i in range(1, count):
                offset = (i / count - 0.5) * road * 0.78
                a = QPointF(cx + nx * inner - tx * offset, cy + ny * inner - ty * offset)
                b = QPointF(cx + nx * outer - tx * offset, cy + ny * outer - ty * offset)
                p.drawLine(a, b)
        # Center/direction separator for each arm.
        p.setPen(QPen(QColor("#e2e8f0"), 1, Qt.PenStyle.DashLine))
        for approach in self.lane_counts:
            bearing = math.radians(APPROACH_BEARINGS.get(approach, 0))
            nx, ny = math.sin(bearing), -math.cos(bearing)
            a = QPointF(cx + nx * road * 0.48, cy + ny * road * 0.48)
            b = QPointF(cx + nx * max(w, h) * 0.72, cy + ny * max(w, h) * 0.72)
            p.drawLine(a, b)
        # Rotated zebra crossings and stop bars at each active approach.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f8fafc"))
        for approach in self.lane_counts:
            bearing = math.radians(APPROACH_BEARINGS.get(approach, 0))
            nx, ny = math.sin(bearing), -math.cos(bearing)
            tx, ty = math.cos(bearing), math.sin(bearing)
            inner = road * 0.49
            outer = road * 0.66
            p.save()
            p.translate(cx, cy)
            p.rotate(math.degrees(bearing))
            # In local coordinates the incoming arm points upward.
            for i in range(8):
                stripe_x = -road * 0.36 + i * road * 0.09
                p.drawRect(int(stripe_x), int(-outer), int(road * 0.045), int(outer-inner))
            p.setPen(QPen(QColor("#ffffff"), 3))
            p.drawLine(int(-road*0.39), int(-inner), int(road*0.39), int(-inner))
            p.restore()
        # Turn arrows sit in their actual lanes, facing the direction of travel.
        for approach in self.lane_counts:
            for lane in range(1, max(1, self.lane_counts.get(approach, 1)) + 1):
                pos = self._lane_center(approach, lane, g)
                turns = self.lane_turns.get((approach, lane), self.TURNS)
                bearing = math.radians(self._approach_bearing(approach))
                x = cx + math.sin(bearing) * road * 0.76
                y = cy - math.cos(bearing) * road * 0.76
                glyphs = self.TURN_GLYPHS.get(approach, {"Прямо": "↑", "Налево": "↶", "Направо": "↷"})
                for j, turn in enumerate(turns):
                    movement_state = self._movement_state(f"{approach}:{lane}:{turn}", stage, active)
                    arrow_color = self.COLORS.get(movement_state, self.COLORS["red"])
                    tangent_x, tangent_y = math.cos(bearing), math.sin(bearing)
                    offset = (j - (len(turns)-1)/2) * 17
                    self._draw_arrow(p, x + tangent_x * offset, y + tangent_y * offset,
                                     glyphs.get(turn, "·"), arrow_color)
        # Signal heads are installed at the stop line, one per lane/approach.
        for approach in self.lane_counts:
            count = max(1, self.lane_counts.get(approach, 1))
            for lane in range(1, count + 1):
                turns = self.lane_turns.get((approach, lane), self.TURNS)
                states = [self._movement_state(f"{approach}:{lane}:{turn}", stage, active) for turn in turns]
                state = "green" if "green" in states else "flash" if "flash" in states else "yellow" if "yellow" in states else "red"
                bearing = math.radians(self._approach_bearing(approach))
                nx, ny = math.sin(bearing), -math.cos(bearing)
                tx, ty = math.cos(bearing), math.sin(bearing)
                lane_offset = self._lane_offset(approach, lane, road)
                # Place each head beside its own lane, just upstream of the
                # stop line; use the same centreline geometry as vehicle paths.
                sx = cx + nx * road * 0.43 - tx * lane_offset + nx * 12
                sy = cy + ny * road * 0.43 - ty * lane_offset + ny * 12
                self._draw_signal_head(p, sx, sy, state, horizontal=abs(math.sin(bearing)) > 0.7)
        # Draw road signs as recognizable, full-size vector pictograms (not code-only icons).
        for sign in self.road_signs:
            approach, code = sign.get("approach"), sign.get("code", "")
            if approach == "Север":
                sx, sy = cx + road * 0.31, top - 58
            elif approach == "Юг":
                sx, sy = cx + road * 0.31, bottom + 59
            elif approach == "Запад":
                sx, sy = left - 59, cy - road * 0.31
            else:
                sx, sy = right + 59, cy - road * 0.31
            self._draw_road_sign(p, sx, sy, code)
            p.save()
            p.setPen(QColor("#0f172a"))
            p.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
            label = f"{code} · {sign.get('name', SIGN_CATALOG.get(code, ('Знак', ''))[0])}"
            p.drawText(int(sx - 68), int(sy + 31), 136, 22,
                       Qt.AlignmentFlag.AlignHCenter | Qt.TextFlag.TextWordWrap, label)
            p.restore()

        # Distinct top-down vehicle silhouettes; sizes are schematic, not to scale.
        for vehicle in self.vehicles:
            pt = self._point_at(vehicle)
            p.save()
            p.translate(pt)
            # Orient the vehicle to the local tangent of its path. This makes the
            # body rotate continuously through left/right turns instead of snapping.
            path = vehicle["path"]
            length = max(1.0, vehicle["length"])
            d = max(0.0, min(length, vehicle["distance"]))
            p0 = path.pointAtPercent(path.percentAtLength(max(0.0, d - 2.0)))
            p1 = path.pointAtPercent(path.percentAtLength(min(length, d + 2.0)))
            from math import atan2, degrees
            angle = degrees(atan2(p1.y() - p0.y(), p1.x() - p0.x()))
            p.rotate(angle)
            kind = vehicle.get("vehicle_type", "car")
            colors = {
                "car": QColor("#38bdf8"), "bus": QColor("#f59e0b"),
                "articulated_bus": QColor("#fb923c"), "truck": QColor("#a78bfa"),
                "lorry": QColor("#e879f9"), "dump_truck": QColor("#f97316")
            }
            body = colors.get(kind, colors["car"])
            p.setPen(QPen(QColor("#102033"), 1))
            p.setBrush(body)
            if kind == "car":
                p.drawRoundedRect(-12, -5, 24, 10, 4, 4)
                p.setBrush(QColor("#dbeafe")); p.drawRoundedRect(-4, -3, 7, 6, 2, 2)
            elif kind == "bus":
                p.drawRoundedRect(-18, -6, 36, 12, 3, 3)
                p.setBrush(QColor("#dbeafe"))
                for x in (-12, -6, 0, 6): p.drawRect(x, -4, 4, 4)
                p.setBrush(QColor("#334155")); p.drawRect(9, -3, 3, 6)
            elif kind == "articulated_bus":
                p.drawRoundedRect(-26, -6, 52, 12, 3, 3)
                p.setBrush(QColor("#dbeafe"))
                for x in (-15, -9, -3, 5, 11): p.drawRect(x, -3, 4, 3)
                p.setBrush(QColor("#475569")); p.drawRect(1, -5, 3, 10)
                p.setPen(QPen(QColor("#102033"), 1)); p.drawLine(2, -4, 2, 4)
            elif kind == "truck":
                p.drawRoundedRect(-17, -6, 17, 12, 3, 3)
                p.setBrush(QColor("#e2e8f0")); p.drawRect(0, -5, 17, 10)
                p.setBrush(QColor("#bae6fd")); p.drawRect(-10, -3, 4, 3)
            elif kind == "lorry":
                p.drawRoundedRect(-22, -6, 13, 12, 3, 3)
                p.setBrush(QColor("#e5e7eb")); p.drawRect(-9, -5, 29, 10)
                p.setBrush(QColor("#bae6fd")); p.drawRect(-15, -3, 4, 3)
                p.setBrush(QColor("#334155"))
                for x in (-13, -3, 7, 13): p.drawEllipse(x, 4, 3, 3)
            else:  # dump truck / tipper
                p.drawRoundedRect(-13, -5, 13, 10, 2, 2)
                p.setBrush(QColor("#fbbf24")); p.drawPolygon([QPointF(0,-4), QPointF(13,-3), QPointF(16,4), QPointF(0,4)])
                p.setBrush(QColor("#bae6fd")); p.drawRect(-10, -3, 4, 3)
            p.restore()
        # Three small pedestrian silhouettes with different clothing/accessory accents.
        for walker in self.walkers:
            pt = self._point_at(walker)
            style = walker.get("style", 0)
            p.save()
            p.translate(pt)
            p.setPen(QPen(QColor("#273449"), 1))
            p.setBrush((QColor("#fcd34d"), QColor("#fda4af"), QColor("#86efac"))[style % 3])
            p.drawEllipse(-3, -5, 6, 6)
            p.setPen(QPen(QColor("#334155"), 2))
            p.drawLine(0, 1, 0, 6)
            p.drawLine(0, 3, -3, 5)
            p.drawLine(0, 3, 3, 5)
            p.drawLine(0, 6, -2, 9)
            p.drawLine(0, 6, 2, 9)
            if style == 1:
                p.setPen(QPen(QColor("#334155"), 1)); p.drawLine(4, 2, 4, 8)
            elif style == 2:
                p.setPen(QPen(QColor("#ffffff"), 2)); p.drawLine(-1, 2, 1, 4)
            p.restore()
        # Small crossing signal indicators are near each zebra crossing.
        for pedestrian in self.pedestrians:
            state = self._movement_state(pedestrian.movement_id, stage, active)
            if pedestrian.crossing == "Север":
                x, y = cx, top - 35
            elif pedestrian.crossing == "Юг":
                x, y = cx, bottom + 35
            elif pedestrian.crossing == "Запад":
                x, y = left - 35, cy
            else:
                x, y = right + 35, cy
            # Pedestrian signal: dark housing plus a distinct person glyph.
            # Avoid the ambiguous persistent red square from the previous painter.
            p.save()
            p.setPen(QPen(QColor("#111827"), 1))
            p.setBrush(QColor("#111827"))
            p.drawRoundedRect(int(x - 8), int(y - 12), 16, 24, 3, 3)
            is_green = state in ("green", "flash")
            lamp = QColor("#22c55e") if is_green else QColor("#ef4444")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(lamp)
            p.drawEllipse(int(x - 5), int(y - 9), 10, 10)
            p.setPen(QPen(QColor("#f8fafc"), 2))
            p.drawEllipse(int(x - 1), int(y - 7), 2, 2)
            p.drawLine(int(x), int(y - 4), int(x), int(y + 1))
            if is_green:
                p.drawLine(int(x), int(y - 2), int(x - 3), int(y + 1))
                p.drawLine(int(x), int(y - 2), int(x + 3), int(y))
                p.drawLine(int(x), int(y + 1), int(x - 3), int(y + 5))
                p.drawLine(int(x), int(y + 1), int(x + 3), int(y + 5))
            else:
                p.drawLine(int(x), int(y - 2), int(x - 3), int(y + 1))
                p.drawLine(int(x), int(y - 2), int(x + 3), int(y + 1))
                p.drawLine(int(x), int(y + 1), int(x - 3), int(y + 5))
                p.drawLine(int(x), int(y + 1), int(x + 3), int(y + 5))
            p.restore()
        p.setPen(QColor("#172033"))
        p.setFont(QFont("Segoe UI", 8))
        footer = ("Воспроизведение событий расчёта" if self.replay_mode else "Демонстрационная анимация") + " · схема условная, не натурная геометрия"
        p.drawText(10, int(h - 10), footer)
