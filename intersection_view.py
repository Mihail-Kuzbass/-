"""Editable schematic intersection view with per-approach lane counts."""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget


class IntersectionView(QWidget):
    APPROACHES = ("Север", "Восток", "Юг", "Запад")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(500, 400)
        self.lane_counts = {name: 2 for name in self.APPROACHES}
        self.selected_approach = "Север"
        self.lane_turns = {}

    def set_lane_count(self, approach, count):
        if approach not in self.lane_counts:
            raise ValueError(f"Неизвестное направление: {approach}")
        self.lane_counts[approach] = max(1, min(8, int(count)))
        self.update()

    def set_lane_turns(self, lane_turns):
        self.lane_turns = dict(lane_turns)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#e8eee8"))
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        road_w = min(w, h) * 0.34
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#50565c"))
        p.drawRect(QRectF(cx-road_w/2, 0, road_w, h))
        p.drawRect(QRectF(0, cy-road_w/2, w, road_w))
        p.setPen(QPen(QColor("#f8f0d7"), 2, Qt.PenStyle.DashLine))
        p.drawLine(int(cx), 0, int(cx), int(cy-road_w/2))
        p.drawLine(int(cx), int(cy+road_w/2), int(cx), h)
        p.drawLine(0, int(cy), int(cx-road_w/2), int(cy))
        p.drawLine(int(cx+road_w/2), int(cy), w, int(cy))
        # Draw lane separators; counts are configurable independently for each approach.
        p.setPen(QPen(QColor("#cbd0d3"), 1))
        for i in range(1, self.lane_counts["Север"]):
            x = cx-road_w/2 + road_w*i/self.lane_counts["Север"]
            p.drawLine(int(x), 0, int(x), int(cy-road_w/2))
        for i in range(1, self.lane_counts["Юг"]):
            x = cx-road_w/2 + road_w*i/self.lane_counts["Юг"]
            p.drawLine(int(x), int(cy+road_w/2), int(x), h)
        for i in range(1, self.lane_counts["Запад"]):
            y = cy-road_w/2 + road_w*i/self.lane_counts["Запад"]
            p.drawLine(0, int(y), int(cx-road_w/2), int(y))
        for i in range(1, self.lane_counts["Восток"]):
            y = cy-road_w/2 + road_w*i/self.lane_counts["Восток"]
            p.drawLine(int(cx+road_w/2), int(y), w, int(y))
        # Show each lane's permitted turn arrows directly on the schematic.
        arrows = {
            "Север": {"Прямо": "↓", "Налево": "→", "Направо": "←"},
            "Юг": {"Прямо": "↑", "Налево": "←", "Направо": "→"},
            "Восток": {"Прямо": "←", "Налево": "↓", "Направо": "↑"},
            "Запад": {"Прямо": "→", "Налево": "↑", "Направо": "↓"},
        }
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.setPen(QColor("#f8fafc"))
        for approach in self.APPROACHES:
            count = self.lane_counts[approach]
            for lane in range(1, count + 1):
                allowed = self.lane_turns.get((approach, lane), ("Прямо", "Налево", "Направо"))
                label = " ".join(arrows[approach][turn] for turn in allowed if turn in arrows[approach])
                pos = (lane - 0.5) / count
                if approach == "Север":
                    x, y = cx-road_w/2+pos*road_w, cy/2
                elif approach == "Юг":
                    x, y = cx-road_w/2+pos*road_w, cy+(h-cy)/2
                elif approach == "Запад":
                    x, y = cx/2, cy-road_w/2+pos*road_w
                else:
                    x, y = cx+(w-cx)/2, cy-road_w/2+pos*road_w
                p.drawText(int(x-16), int(y+4), label)
        p.setPen(QColor("#202020"))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        p.drawText(12, 24, f"СЕВЕР · {self.lane_counts['Север']} полос(ы)")
        p.drawText(max(12, w-170), 24, f"ВОСТОК · {self.lane_counts['Восток']}")
        p.drawText(12, h-14, f"ЗАПАД · {self.lane_counts['Запад']}")
        p.drawText(max(12, w-140), h-14, f"ЮГ · {self.lane_counts['Юг']}")
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(12, h-40, "Стрелки в полосах показывают разрешённые манёвры; схема условная")
