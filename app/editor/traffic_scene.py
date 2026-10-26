"""Animated, lane-aware schematic for a signalized intersection.

Vehicles follow complete turn-specific paths across the junction; pedestrians traverse
the entire marked crossing. This remains a visual teaching model, not calibrated
microsimulation or a substitute for a surveyed intersection plan.
"""
from math import hypot
from PySide6.QtCore import Qt, QTimer, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath
from PySide6.QtWidgets import QWidget


class TrafficScene(QWidget):
    APPROACHES = ("Север", "Восток", "Юг", "Запад")
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
        self.lane_counts = {name: 2 for name in self.APPROACHES}
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

    def configure(self, phases, movements, pedestrians, cycle_seconds, lane_counts=None, lane_turns=None, result=None):
        self.pause()
        self.phases = list(phases)
        self.movements = list(movements)
        self.pedestrians = list(pedestrians)
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
            # Cars approach even on red, stop at the stop line, and only enter
            # when the matching movement has a service/release event.
            proposed = vehicle["distance"] + vehicle["speed"] * dt
            if vehicle["distance"] < vehicle["stop_distance"]:
                vehicle["distance"] = min(proposed, vehicle["stop_distance"])
            elif vehicle.get("released", False):
                vehicle["distance"] = proposed
            else:
                vehicle["distance"] = vehicle["stop_distance"]
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
                vehicle = next((v for v in self.vehicles if v["key"] == key and not v.get("released", False)
                                and v["distance"] >= v["stop_distance"] - 1.0), None)
                if vehicle is None:
                    vehicle = next((v for v in self.vehicles if v["key"] == key and not v.get("released", False)), None)
                if vehicle is not None:
                    vehicle["released"] = True
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
        return active

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
                    length = path.length()
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

    def _layout(self):
        w, h = float(self.width()), float(self.height())
        road_w = min(w * 0.47, h * 0.58)
        cx, cy = w / 2, h / 2 + 5
        left, right = cx - road_w / 2, cx + road_w / 2
        top, bottom = cy - road_w / 2, cy + road_w / 2
        return {"w": w, "h": h, "cx": cx, "cy": cy, "road": road_w,
                "left": left, "right": right, "top": top, "bottom": bottom}

    def _lane_center(self, approach, lane, g):
        count = max(1, self.lane_counts.get(approach, 1))
        pos = (lane - 0.5) / count
        # Each approach occupies its own half of the carriageway so opposing
        # directions never share a lane center.
        if approach == "Север":
            return QPointF(g["cx"] + pos * g["road"] / 2, g["top"])
        if approach == "Юг":
            return QPointF(g["left"] + pos * g["road"] / 2, g["bottom"])
        if approach == "Запад":
            return QPointF(g["left"], g["top"] + pos * g["road"] / 2)
        return QPointF(g["right"], g["cy"] + pos * g["road"] / 2)

    def _vehicle_path(self, approach, lane, turn):
        g = self._layout()
        center = self._lane_center(approach, lane, g)
        cx, cy, left, right, top, bottom = (g[k] for k in ("cx", "cy", "left", "right", "top", "bottom"))
        exit_margin = 95.0
        approach_margin = 115.0
        stop_offset = 34.0
        if approach == "Север":
            start = QPointF(center.x(), top - approach_margin)
            stop = QPointF(center.x(), top - stop_offset)
            entry = QPointF(center.x(), top)
            exit_side = {"Прямо": "Юг", "Налево": "Восток", "Направо": "Запад"}[turn]
        elif approach == "Юг":
            start = QPointF(center.x(), bottom + approach_margin)
            stop = QPointF(center.x(), bottom + stop_offset)
            entry = QPointF(center.x(), bottom)
            exit_side = {"Прямо": "Север", "Налево": "Запад", "Направо": "Восток"}[turn]
        elif approach == "Запад":
            start = QPointF(left - approach_margin, center.y())
            stop = QPointF(left - stop_offset, center.y())
            entry = QPointF(left, center.y())
            exit_side = {"Прямо": "Восток", "Налево": "Север", "Направо": "Юг"}[turn]
        else:
            start = QPointF(right + approach_margin, center.y())
            stop = QPointF(right + stop_offset, center.y())
            entry = QPointF(right, center.y())
            exit_side = {"Прямо": "Запад", "Налево": "Юг", "Направо": "Север"}[turn]
        exit_pos = (lane - 0.5) / max(1, self.lane_counts.get(approach, 1))
        if exit_side == "Юг":
            end = QPointF(cx + exit_pos * g["road"] / 2, g["h"] + exit_margin)
        elif exit_side == "Север":
            end = QPointF(left + exit_pos * g["road"] / 2, -exit_margin)
        elif exit_side == "Восток":
            end = QPointF(g["w"] + exit_margin, top + exit_pos * g["road"] / 2)
        else:
            end = QPointF(-exit_margin, cy + exit_pos * g["road"] / 2)
        path = QPainterPath(start)
        path.lineTo(stop)
        stop_distance = hypot(stop.x() - start.x(), stop.y() - start.y())
        path.lineTo(entry)
        if approach == exit_side:
            path.lineTo(end)
            return path, stop_distance
        c1 = QPointF(entry.x(), cy) if approach in ("Север", "Юг") else QPointF(cx, entry.y())
        c2 = QPointF(end.x(), cy) if exit_side in ("Север", "Юг") else QPointF(cx, end.y())
        path.cubicTo(c1, c2, end)
        return path, stop_distance

    def _pedestrian_path(self, crossing):
        g = self._layout()
        cx, cy, left, right, top, bottom, road = (g[k] for k in ("cx", "cy", "left", "right", "top", "bottom", "road"))
        margin = 30.0
        path = QPainterPath()
        if crossing == "Север":
            # North-leg crosswalk runs left-to-right across the north approach.
            path.moveTo(left - margin, top - 16)
            path.lineTo(right + margin, top - 16)
        elif crossing == "Юг":
            # South-leg crosswalk runs right-to-left across the south approach.
            path.moveTo(right + margin, bottom + 16)
            path.lineTo(left - margin, bottom + 16)
        elif crossing == "Запад":
            # West-leg crosswalk runs from the lower curb to the upper curb.
            path.moveTo(left - 16, bottom + margin)
            path.lineTo(left - 16, top - margin)
        else:
            # East-leg crosswalk runs from the upper curb to the lower curb.
            path.moveTo(right + 16, top - margin)
            path.lineTo(right + 16, bottom + margin)
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
        p.drawText(10, 35, f"Время: {self.phase_elapsed:.1f} с · Скорость: {self.speed_multiplier}× · "
                            f"{'АНИМАЦИЯ' if self.running else 'ПАУЗА'}")
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#3b434d"))
        p.drawRect(int(left), 0, int(road), int(h))
        p.drawRect(0, int(top), int(w), int(road))
        # Dashed lane markings on each approach.
        p.setPen(QPen(QColor("#b9c2ca"), 1, Qt.PenStyle.DashLine))
        for approach in ("Север", "Юг"):
            count = max(1, self.lane_counts.get(approach, 1))
            for i in range(1, count):
                x = (cx + road * i / (2 * count)) if approach == "Север" else (left + road * i / (2 * count))
                if approach == "Север":
                    p.drawLine(int(x), 42, int(x), int(top))
                else:
                    p.drawLine(int(x), int(bottom), int(x), int(h - 42))
        for approach in ("Запад", "Восток"):
            count = max(1, self.lane_counts.get(approach, 1))
            for i in range(1, count):
                y = (top + road * i / (2 * count)) if approach == "Запад" else (cy + road * i / (2 * count))
                if approach == "Запад":
                    p.drawLine(0, int(y), int(left), int(y))
                else:
                    p.drawLine(int(right), int(y), int(w), int(y))
        # Center lines separate opposing directions.
        p.setPen(QPen(QColor("#e2e8f0"), 1, Qt.PenStyle.DashLine))
        p.drawLine(int(cx), 42, int(cx), int(top))
        p.drawLine(int(cx), int(bottom), int(cx), int(h - 42))
        p.drawLine(0, int(cy), int(left), int(cy))
        p.drawLine(int(right), int(cy), int(w), int(cy))
        # Crosswalks with zebra stripes and stop lines.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f8fafc"))
        stripe_count = 8
        for i in range(stripe_count):
            off = i * road / stripe_count
            stripe = road / (stripe_count * 2)
            p.drawRect(int(left + off), int(top - 25), int(stripe), 18)
            p.drawRect(int(left + off), int(bottom + 7), int(stripe), 18)
            p.drawRect(int(left - 25), int(top + off), 18, int(stripe))
            p.drawRect(int(right + 7), int(top + off), 18, int(stripe))
        p.setPen(QPen(QColor("#ffffff"), 3))
        p.drawLine(int(left), int(top - 29), int(right), int(top - 29))
        p.drawLine(int(left), int(bottom + 29), int(right), int(bottom + 29))
        p.drawLine(int(left - 29), int(top), int(left - 29), int(bottom))
        p.drawLine(int(right + 29), int(top), int(right + 29), int(bottom))
        # Turn arrows sit in their actual lanes, facing the direction of travel.
        for approach in self.APPROACHES:
            for lane in range(1, max(1, self.lane_counts.get(approach, 1)) + 1):
                pos = self._lane_center(approach, lane, g)
                turns = self.lane_turns.get((approach, lane), self.TURNS)
                if approach == "Север":
                    x, y = pos.x(), top - 50
                elif approach == "Юг":
                    x, y = pos.x(), bottom + 52
                elif approach == "Запад":
                    x, y = left - 52, pos.y()
                else:
                    x, y = right + 52, pos.y()
                for j, turn in enumerate(turns):
                    movement_state = self._movement_state(f"{approach}:{lane}:{turn}", stage, active)
                    arrow_color = self.COLORS.get(movement_state, self.COLORS["red"])
                    self._draw_arrow(p, x + (j - (len(turns)-1)/2) * 17 if approach in ("Север", "Юг") else x,
                                     y if approach in ("Север", "Юг") else y + (j - (len(turns)-1)/2) * 17,
                                     self.TURN_GLYPHS[approach].get(turn, "·"), arrow_color)
        # Signal heads are installed at the stop line, one per lane/approach.
        for approach in self.APPROACHES:
            count = max(1, self.lane_counts.get(approach, 1))
            for lane in range(1, count + 1):
                turns = self.lane_turns.get((approach, lane), self.TURNS)
                states = [self._movement_state(f"{approach}:{lane}:{turn}", stage, active) for turn in turns]
                state = "green" if "green" in states else "flash" if "flash" in states else "yellow" if "yellow" in states else "red"
                pos = self._lane_center(approach, lane, g)
                if approach == "Север":
                    self._draw_signal_head(p, pos.x(), top - 12, state)
                elif approach == "Юг":
                    self._draw_signal_head(p, pos.x(), bottom + 12, state)
                elif approach == "Запад":
                    self._draw_signal_head(p, left - 12, pos.y(), state, horizontal=True)
                else:
                    self._draw_signal_head(p, right + 12, pos.y(), state, horizontal=True)
        # Distinct top-down vehicle silhouettes; sizes are schematic, not to scale.
        for vehicle in self.vehicles:
            pt = self._point_at(vehicle)
            p.save()
            p.translate(pt)
            horizontal = vehicle["key"].startswith(("Восток:", "Запад:"))
            if horizontal:
                p.rotate(0)
            else:
                p.rotate(90)
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
                p.drawRoundedRect(-9, -4, 18, 8, 3, 3)
                p.setBrush(QColor("#dbeafe")); p.drawRoundedRect(-4, -3, 7, 6, 2, 2)
            elif kind == "bus":
                p.drawRoundedRect(-14, -5, 28, 10, 2, 2)
                p.setBrush(QColor("#dbeafe"))
                for x in (-9, -3, 3, 9): p.drawRect(x, -3, 3, 3)
                p.setBrush(QColor("#334155")); p.drawRect(9, -3, 3, 6)
            elif kind == "articulated_bus":
                p.drawRoundedRect(-20, -5, 40, 10, 2, 2)
                p.setBrush(QColor("#dbeafe"))
                for x in (-15, -9, -3, 5, 11): p.drawRect(x, -3, 4, 3)
                p.setBrush(QColor("#475569")); p.drawRect(1, -5, 3, 10)
                p.setPen(QPen(QColor("#102033"), 1)); p.drawLine(2, -4, 2, 4)
            elif kind == "truck":
                p.drawRoundedRect(-13, -5, 13, 10, 2, 2)
                p.setBrush(QColor("#e2e8f0")); p.drawRect(0, -4, 13, 8)
                p.setBrush(QColor("#bae6fd")); p.drawRect(-10, -3, 4, 3)
            elif kind == "lorry":
                p.drawRoundedRect(-17, -5, 10, 10, 2, 2)
                p.setBrush(QColor("#e5e7eb")); p.drawRect(-7, -4, 23, 8)
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
            p.setPen(QPen(QColor("#111827"), 1))
            p.setBrush(self.COLORS.get(state, self.COLORS["red"]))
            p.drawRoundedRect(int(x - 5), int(y - 5), 10, 10, 2, 2)
        p.setPen(QColor("#172033"))
        p.setFont(QFont("Segoe UI", 8))
        footer = ("Воспроизведение событий расчёта" if self.replay_mode else "Демонстрационная анимация") + " · схема условная, не натурная геометрия"
        p.drawText(10, int(h - 10), footer)
