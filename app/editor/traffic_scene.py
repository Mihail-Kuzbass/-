"""Animated top-down intersection view for the detailed scenario tab."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget


class TrafficScene(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(520, 380)
        self.running = False
        self.tick = 0
        self.phase_name = "Готов к запуску"
        self.phase_index = 0
        self.phase_elapsed = 0
        self.cycle_seconds = 1
        self.phases = []
        self.movements = []
        self.pedestrians = []
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._advance)

    def configure(self, phases, movements, pedestrians, cycle_seconds):
        self.phases = list(phases)
        self.movements = list(movements)
        self.pedestrians = list(pedestrians)
        self.cycle_seconds = max(1, cycle_seconds)
        self.tick = 0
        self.phase_index = 0
        self.phase_elapsed = 0
        self._update_phase()
        self.update()

    def start(self):
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
        self.phase_elapsed = 0
        self._update_phase()
        self.update()

    def _advance(self):
        self.tick += 1
        self.phase_elapsed += 0.1
        if self.phases:
            current = self.phases[self.phase_index]
            if self.phase_elapsed >= max(1, current.duration_seconds):
                self.phase_elapsed = 0
                self.phase_index = (self.phase_index + 1) % len(self.phases)
            self._update_phase()
        self.update()

    def _update_phase(self):
        if self.phases:
            self.phase_name = self.phases[self.phase_index].name
        else:
            self.phase_name = "Нет фаз"

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#e9f0e8"))
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        rw = min(w, h) * 0.36
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#343a40"))
        p.drawRect(int(cx-rw/2), 0, int(rw), h)
        p.drawRect(0, int(cy-rw/2), w, int(rw))
        p.setPen(QPen(QColor("#f7f2d0"), 2, Qt.PenStyle.DashLine))
        p.drawLine(int(cx), 0, int(cx), int(cy-rw/2))
        p.drawLine(int(cx), int(cy+rw/2), int(cx), h)
        p.drawLine(0, int(cy), int(cx-rw/2), int(cy))
        p.drawLine(int(cx+rw/2), int(cy), w, int(cy))
        # Crosswalks
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#e9ecef"))
        for i in range(6):
            offset = -rw/2 + i * rw/6
            p.drawRect(int(cx+offset), int(cy-rw/2-12), int(rw/12), 8)
            p.drawRect(int(cx+offset), int(cy+rw/2+4), int(rw/12), 8)
            p.drawRect(int(cx-rw/2-12), int(cy+offset), 8, int(rw/12))
            p.drawRect(int(cx+rw/2+4), int(cy+offset), 8, int(rw/12))
        # Signal status is based on actual movement assignments in the current phase.
        active = set(self.phases[self.phase_index].movements) if self.phases else set()
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        p.setPen(QColor("#ffffff"))
        p.drawText(8, 18, f"Фаза: {self.phase_name}   |   {'ПАУЗА' if not self.running else 'СИМУЛЯЦИЯ'}")
        # Flow lanes: animated colored car tokens, with movement/lane identity shown by the legend.
        colors = {"Север": QColor("#56b4e9"), "Юг": QColor("#56b4e9"),
                  "Восток": QColor("#e69f00"), "Запад": QColor("#e69f00")}
        for idx, movement in enumerate(self.movements):
            approach = movement.approach
            is_green = movement.movement_id in active
            color = colors.get(approach, QColor("#56b4e9")) if is_green else QColor("#a6a6a6")
            phase = (self.tick * 2 + idx * 13) % 100 / 100.0
            if approach == "Север":
                x, y = cx - rw*0.22 + (movement.lane-1)*7, phase * (cy-rw/2-22)
            elif approach == "Юг":
                x, y = cx + rw*0.22 - (movement.lane-1)*7, h - phase * (cy-rw/2-22)
            elif approach == "Запад":
                x, y = phase * (cx-rw/2-22), cy + rw*0.22 - (movement.lane-1)*7
            else:
                x, y = w - phase * (cx-rw/2-22), cy - rw*0.22 + (movement.lane-1)*7
            p.setPen(QPen(QColor("#20252a"), 1))
            p.setBrush(color)
            p.drawRoundedRect(int(x-5), int(y-8), 10, 16, 3, 3)
        # Pedestrians appear as small white markers near each crossing.
        p.setPen(QPen(QColor("#222222"), 1))
        p.setBrush(QColor("#ffffff"))
        for idx, pedestrian in enumerate(self.pedestrians):
            active_ped = pedestrian.movement_id in active
            for j in range(min(7, max(1, pedestrian.pedestrians_per_hour // 250 + 1))):
                offset = ((self.tick + idx*9 + j*11) % 35) - 17
                if pedestrian.crossing in ("Север", "Юг"):
                    x, y = cx + offset, cy + (-rw/2-17 if pedestrian.crossing == "Север" else rw/2+17)
                else:
                    x, y = cx + (-rw/2-17 if pedestrian.crossing == "Запад" else rw/2+17), cy + offset
                p.setBrush(QColor("#f0e442") if active_ped else QColor("#a0a0a0"))
                p.drawEllipse(int(x-3), int(y-3), 6, 6)
        p.setPen(QColor("#20252a"))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(8, h-8, "Цветные ТС — разрешённые назначения текущей фазы; серые — ожидание. Схема условная.")
