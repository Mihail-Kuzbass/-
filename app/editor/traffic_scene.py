"""Lane-aware animated signalized intersection schematic.

The view is intentionally schematic, but signal colors and phase changes are driven by
individual lane-turn assignments in the active signal plan.
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget


class TrafficScene(QWidget):
    APPROACHES = ("Север", "Восток", "Юг", "Запад")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(560, 420)
        self.running = False
        self.tick = 0
        self.phase_name = "Готов к запуску"
        self.phase_index = 0
        self.phase_elapsed = 0.0
        self.cycle_seconds = 1
        self.phases = []
        self.movements = []
        self.pedestrians = []
        self.lane_counts = {name: 2 for name in self.APPROACHES}
        self.lane_turns = {}
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._advance)

    def configure(self, phases, movements, pedestrians, cycle_seconds, lane_counts=None, lane_turns=None):
        self.phases = list(phases)
        self.movements = list(movements)
        self.pedestrians = list(pedestrians)
        self.cycle_seconds = max(1, int(cycle_seconds))
        if lane_counts:
            self.lane_counts = dict(lane_counts)
        if lane_turns is not None:
            self.lane_turns = dict(lane_turns)
        self.tick = 0
        self.phase_index = 0
        self.phase_elapsed = 0.0
        self._update_phase()
        self.update()

    def start(self):
        if not self.phases:
            return
        self.running = True
        self.timer.start()
        self.update()

    def pause(self):
        self.running = False
        self.timer.stop()
        self.update()

    def reset(self):
        self.pause()
        self.tick = 0
        self.phase_index = 0
        self.phase_elapsed = 0.0
        self._update_phase()
        self.update()

    def _advance(self):
        self.tick += 1
        self.phase_elapsed += 0.1
        if self.phases:
            current = self.phases[self.phase_index]
            if self.phase_elapsed >= max(1, current.duration_seconds):
                self.phase_elapsed = 0.0
                self.phase_index = (self.phase_index + 1) % len(self.phases)
            self._update_phase()
        self.update()

    def _update_phase(self):
        self.phase_name = self.phases[self.phase_index].name if self.phases else "Нет фаз"

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

    def _movement_state(self, movement_id, stage, active):
        if movement_id not in active:
            return "red"
        if stage in ("green", "flash", "yellow"):
            return stage
        return "red"

    @staticmethod
    def _color(state):
        return {
            "green": QColor("#22c55e"),
            "flash": QColor("#86efac"),
            "yellow": QColor("#facc15"),
            "red": QColor("#ef4444"),
            "intergreen": QColor("#64748b"),
        }.get(state, QColor("#ef4444"))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#eaf0e8"))
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        road_w = min(w, h) * 0.38
        left, right = cx-road_w/2, cx+road_w/2
        top, bottom = cy-road_w/2, cy+road_w/2
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#39434d"))
        p.drawRect(int(left), 0, int(road_w), h)
        p.drawRect(0, int(top), w, int(road_w))

        # Lane separators are drawn using the independently configured lane count for each approach.
        p.setPen(QPen(QColor("#cbd5e1"), 1, Qt.PenStyle.DashLine))
        for approach in ("Север", "Юг"):
            count = max(1, self.lane_counts.get(approach, 1))
            for i in range(1, count):
                x = left + road_w * i / count
                if approach == "Север":
                    p.drawLine(int(x), 0, int(x), int(top))
                else:
                    p.drawLine(int(x), int(bottom), int(x), h)
        for approach in ("Запад", "Восток"):
            count = max(1, self.lane_counts.get(approach, 1))
            for i in range(1, count):
                y = top + road_w * i / count
                if approach == "Запад":
                    p.drawLine(0, int(y), int(left), int(y))
                else:
                    p.drawLine(int(right), int(y), w, int(y))

        # Stop lines and crosswalks.
        p.setPen(QPen(QColor("#ffffff"), 3))
        p.drawLine(int(left), int(top-7), int(right), int(top-7))
        p.drawLine(int(left), int(bottom+7), int(right), int(bottom+7))
        p.drawLine(int(left-7), int(top), int(left-7), int(bottom))
        p.drawLine(int(right+7), int(top), int(right+7), int(bottom))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#f8fafc"))
        for i in range(7):
            off = -road_w/2 + i*road_w/7
            p.drawRect(int(cx+off), int(top-22), int(road_w/14), 10)
            p.drawRect(int(cx+off), int(bottom+12), int(road_w/14), 10)
            p.drawRect(int(left-22), int(cy+off), 10, int(road_w/14))
            p.drawRect(int(right+12), int(cy+off), 10, int(road_w/14))

        stage = self._stage()
        active = self._active_ids()
        phase = self.phases[self.phase_index] if self.phases else None
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        p.setPen(QColor("#ffffff"))
        status = {"green": "ЗЕЛЁНЫЙ", "flash": "МИГАЮЩИЙ ЗЕЛЁНЫЙ",
                  "yellow": "ЖЁЛТЫЙ", "red": "КРАСНЫЙ", "intergreen": "МЕЖФАЗНЫЙ ИНТЕРВАЛ"}[stage]
        p.drawText(8, 18, f"ФАЗА {self.phase_index+1 if phase else 0}: {self.phase_name} · {status}")
        if phase:
            p.drawText(8, 35, f"Время фазы: {self.phase_elapsed:.1f} / {phase.duration_seconds} с · {'РАБОТАЕТ' if self.running else 'ПАУЗА'}")

        # Each lane has its own turn labels, signal lamp, and vehicle token.
        for approach in self.APPROACHES:
            count = max(1, self.lane_counts.get(approach, 1))
            for lane in range(1, count+1):
                turns = self.lane_turns.get((approach, lane), ("Прямо", "Налево", "Направо"))
                for turn in turns:
                    key = f"{approach}:{lane}:{turn}"
                    state = self._movement_state(key, stage, active)
                    pos = (lane - 0.5) / count
                    if approach == "Север":
                        x, stop_y = left + pos*road_w, top-10
                        car_x, car_y = x, (top-38 if state == "red" else (top-55-(self.tick%16)*4))
                        arrow_x, arrow_y = x, top-65
                    elif approach == "Юг":
                        x, stop_y = left + pos*road_w, bottom+10
                        car_x, car_y = x, (bottom+38 if state == "red" else (bottom+55+(self.tick%16)*4))
                        arrow_x, arrow_y = x, bottom+65
                    elif approach == "Запад":
                        y, stop_x = top + pos*road_w, left-10
                        car_x, car_y = (left-38 if state == "red" else (left-55-(self.tick%16)*4)), y
                        arrow_x, arrow_y = left-65, y
                    else:
                        y, stop_x = top + pos*road_w, right+10
                        car_x, car_y = (right+38 if state == "red" else (right+55+(self.tick%16)*4)), y
                        arrow_x, arrow_y = right+65, y
                    # Small turn arrow label
                    p.setPen(QColor("#ffffff"))
                    p.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
                    label = {"Прямо": "↑", "Налево": "←", "Направо": "→"}[turn]
                    if approach == "Юг": label = {"Прямо": "↓", "Налево": "→", "Направо": "←"}[turn]
                    if approach == "Восток": label = {"Прямо": "←", "Налево": "↓", "Направо": "↑"}[turn]
                    if approach == "Запад": label = {"Прямо": "→", "Налево": "↑", "Направо": "↓"}[turn]
                    p.drawText(int(arrow_x-4), int(arrow_y+3), label)
                    # Lamp is beside the stop line for this exact lane/turn.
                    p.setPen(QPen(QColor("#111827"), 1))
                    p.setBrush(self._color(state))
                    if approach in ("Север", "Юг"):
                        p.drawEllipse(int(x-4), int(stop_y-4), 8, 8)
                    else:
                        p.drawEllipse(int(stop_x-4), int(y-4), 8, 8)
                    # Vehicle movement is stopped at the stop line on red/yellow/intergreen.
                    if state in ("green", "flash") and self.running:
                        p.setBrush(QColor("#38bdf8"))
                    else:
                        p.setBrush(QColor("#94a3b8"))
                    if approach in ("Север", "Юг"):
                        p.drawRoundedRect(int(car_x-5), int(car_y-8), 10, 16, 3, 3)
                    else:
                        p.drawRoundedRect(int(car_x-8), int(car_y-5), 16, 10, 3, 3)

        # Pedestrian crossing lamps are independent phase assignments.
        for idx, pedestrian in enumerate(self.pedestrians):
            state = self._movement_state(pedestrian.movement_id, stage, active)
            p.setPen(QPen(QColor("#111827"), 1))
            p.setBrush(self._color(state))
            if pedestrian.crossing == "Север":
                x, y = cx, top-28
            elif pedestrian.crossing == "Юг":
                x, y = cx, bottom+28
            elif pedestrian.crossing == "Запад":
                x, y = left-28, cy
            else:
                x, y = right+28, cy
            p.drawRect(int(x-5), int(y-5), 10, 10)
            p.setPen(QColor("#fef3c7") if state in ("green", "flash") else QColor("#d1d5db"))
            for j in range(min(5, max(1, pedestrian.pedestrians_per_hour // 300 + 1))):
                offset = ((self.tick*2 + idx*7 + j*11) % 26) - 13
                if pedestrian.crossing in ("Север", "Юг"):
                    px, py = cx+offset, y
                else:
                    px, py = x, cy+offset
                p.drawEllipse(int(px-2), int(py-2), 4, 4)

        p.setPen(QColor("#111827"))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(8, h-8, "Стрелки и лампы привязаны к полосам/назначениям фазы; схема геометрически условная.")
