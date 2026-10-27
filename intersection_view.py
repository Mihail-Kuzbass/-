"""Schematic intersection view with selectable geometry templates."""
import math
from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QPainter, QColor, QPen, QFont, QPainterPath
from PySide6.QtWidgets import QWidget


class IntersectionView(QWidget):
    APPROACHES = ("Север", "Восток", "Юг", "Запад")
    TEMPLATES = {
        "Классический 4-конечный": 4,
        "Т-образный": 3,
        "С дугообразными подходами": 4,
        "5-конечный": 5,
        "6-конечный": 6,
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(500, 400)
        self.lane_counts = {name: 2 for name in self.APPROACHES}
        self.selected_approach = "Север"
        self.lane_turns = {}
        self.template = "Классический 4-конечный"

    def set_template(self, template):
        if template not in self.TEMPLATES:
            raise ValueError(f"Неизвестный тип перекрёстка: {template}")
        self.template = template
        self.update()

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
        road_w = min(w, h) * 0.22
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#50565c"))

        if self.template == "Классический 4-конечный":
            p.drawRect(QRectF(cx-road_w/2, 0, road_w, h))
            p.drawRect(QRectF(0, cy-road_w/2, w, road_w))
            p.setPen(QPen(QColor("#f8f0d7"), 2, Qt.PenStyle.DashLine))
            p.drawLine(int(cx), 0, int(cx), int(cy-road_w/2))
            p.drawLine(int(cx), int(cy+road_w/2), int(cx), h)
            p.drawLine(0, int(cy), int(cx-road_w/2), int(cy))
            p.drawLine(int(cx+road_w/2), int(cy), w, int(cy))
        else:
            if self.template == "Т-образный":
                angles = [-90, 0, 180]
            elif self.template == "5-конечный":
                angles = [-90, -18, 54, 126, 198]
            elif self.template == "6-конечный":
                angles = [-90, -30, 30, 90, 150, 210]
            else:
                angles = [-90, 0, 90, 180]
            radius = min(w, h) * 0.48
            for angle in angles:
                rad = math.radians(angle)
                ex, ey = cx + math.cos(rad)*radius, cy + math.sin(rad)*radius
                path = QPainterPath(QPointF(cx, cy))
                if self.template == "С дугообразными подходами":
                    control = QPointF(cx + math.cos(rad+0.25)*radius*0.62,
                                      cy + math.sin(rad+0.25)*radius*0.62)
                    path.quadTo(control, QPointF(ex, ey))
                else:
                    path.lineTo(ex, ey)
                p.setPen(QPen(QColor("#50565c"), road_w, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.RoundJoin))
                p.drawPath(path)
                p.setPen(QPen(QColor("#f8f0d7"), 2, Qt.PenStyle.DashLine))
                p.drawLine(QPointF(cx + math.cos(rad)*road_w*0.55,
                                   cy + math.sin(rad)*road_w*0.55),
                           QPointF(cx + math.cos(rad)*(radius-road_w*0.65),
                                   cy + math.sin(rad)*(radius-road_w*0.65)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#50565c"))
            p.drawEllipse(QPointF(cx, cy), road_w*0.53, road_w*0.53)

        arrows = {
            "Север": {"Прямо": "↓", "Налево": "→", "Направо": "←"},
            "Юг": {"Прямо": "↑", "Налево": "←", "Направо": "→"},
            "Восток": {"Прямо": "←", "Налево": "↓", "Направо": "↑"},
            "Запад": {"Прямо": "→", "Налево": "↑", "Направо": "↓"},
        }
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        p.setPen(QColor("#f8fafc"))
        for approach in self.APPROACHES:
            if self.template == "Т-образный" and approach == "Юг":
                continue
            count = self.lane_counts[approach]
            for lane in range(1, count + 1):
                allowed = self.lane_turns.get((approach, lane), ("Прямо", "Налево", "Направо"))
                label = " ".join(arrows[approach][turn] for turn in allowed if turn in arrows[approach])
                pos = (lane - 0.5) / count
                if approach == "Север":
                    x, y = cx-pos*road_w/2, max(70, cy/2)
                elif approach == "Юг":
                    x, y = cx+pos*road_w/2, cy+(h-cy)/2
                elif approach == "Запад":
                    x, y = cx/2, cy+pos*road_w/2
                else:
                    x, y = cx+(w-cx)/2, cy-pos*road_w/2
                p.drawText(int(x-16), int(y+4), label)
        p.setPen(QColor("#202020"))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        p.drawText(12, 24, self.template)
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(12, h-14, "Схема условная; проверьте геометрию, полосы и манёвры")
