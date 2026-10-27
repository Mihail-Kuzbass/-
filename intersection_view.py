"""Editable intersection schematic with dynamic three-to-six-arm templates."""
import math
from PySide6.QtCore import Qt, QPointF, QPen
from PySide6.QtGui import QPainter, QColor, QFont, QPainterPath
from PySide6.QtWidgets import QWidget


class IntersectionView(QWidget):
    APPROACHES = ("Север", "Северо-восток", "Восток", "Юго-восток",
                  "Юг", "Юго-запад", "Запад", "Северо-запад")
    BEARINGS = {"Север": 0, "Северо-восток": 45, "Восток": 90,
                "Юго-восток": 135, "Юг": 180, "Юго-запад": 225,
                "Запад": 270, "Северо-запад": 315}
    TEMPLATE_APPROACHES = {
        "Классический 4-конечный": ("Север", "Восток", "Юг", "Запад"),
        "Т-образный": ("Север", "Восток", "Запад"),
        "С дугообразными подходами": ("Север", "Восток", "Юг", "Запад"),
        "5-конечный": ("Север", "Восток", "Юго-восток", "Юго-запад", "Запад"),
        "6-конечный": ("Север", "Северо-восток", "Юго-восток", "Юг", "Юго-запад", "Северо-запад"),
    }
    TEMPLATES = {name: len(arms) for name, arms in TEMPLATE_APPROACHES.items()}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(500, 400)
        self.lane_counts = {name: 2 for name in self.APPROACHES}
        self.lane_turns = {}
        self.template = "Классический 4-конечный"

    @property
    def active_approaches(self):
        return self.TEMPLATE_APPROACHES[self.template]

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
        road_width = min(w, h) * 0.19
        radius = min(w, h) * 0.47
        p.setPen(QPen(QColor("#50565c"), road_width, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.RoundJoin))
        for approach in self.active_approaches:
            bearing = math.radians(self.BEARINGS[approach])
            ex, ey = cx + math.sin(bearing)*radius, cy - math.cos(bearing)*radius
            path = QPainterPath(QPointF(cx, cy))
            if self.template == "С дугообразными подходами":
                control = QPointF(cx + math.sin(bearing+0.18)*radius*0.58,
                                  cy - math.cos(bearing+0.18)*radius*0.58)
                path.quadTo(control, QPointF(ex, ey))
            else:
                path.lineTo(ex, ey)
            p.drawPath(path)
            p.setPen(QPen(QColor("#f8f0d7"), 2, Qt.PenStyle.DashLine))
            p.drawLine(QPointF(cx + math.sin(bearing)*road_width*0.55,
                               cy - math.cos(bearing)*road_width*0.55),
                       QPointF(cx + math.sin(bearing)*(radius-road_width*0.65),
                               cy - math.cos(bearing)*(radius-road_width*0.65)))
            p.setPen(QPen(QColor("#50565c"), road_width, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.RoundJoin))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#50565c"))
        p.drawEllipse(QPointF(cx, cy), road_width*0.53, road_width*0.53)
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.setPen(QColor("#f8fafc"))
        for approach in self.active_approaches:
            bearing = math.radians(self.BEARINGS[approach])
            count = self.lane_counts[approach]
            allowed = self.lane_turns
            label_parts = []
            for lane in range(1, count+1):
                turns = allowed.get((approach, lane), ("Прямо", "Налево", "Направо"))
                glyphs = {"Прямо": "↑", "Налево": "↶", "Направо": "↷"}
                label_parts.append("".join(glyphs[t] for t in turns if t in glyphs))
            tx = cx + math.sin(bearing)*(radius*0.66)
            ty = cy - math.cos(bearing)*(radius*0.66)
            p.drawText(int(tx-42), int(ty+4), f"{approach}: " + " | ".join(label_parts))
        p.setPen(QColor("#202020"))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        p.drawText(12, 24, f"{self.template} · {len(self.active_approaches)} подхода")
        p.setFont(QFont("Segoe UI", 9))
        p.drawText(12, h-14, "Схема условная; геометрию, полосы и манёвры необходимо проверить")
