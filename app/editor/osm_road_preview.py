"""Simple, attributed preview of imported OpenStreetMap road geometry."""
from math import cos, radians
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget


class OSMRoadPreview(QWidget):
    """Render imported GeoJSON LineString/MultiLineString roads in a local view."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.setMinimumWidth(420)
        self._features = []
        self._bounds = None
        self.setToolTip("Данные OpenStreetMap. Геометрия дорог не содержит гарантированных полос и фаз.")

    def set_data(self, geojson):
        self._features = []
        for feature in (geojson or {}).get("features", []):
            geometry = feature.get("geometry") or {}
            kind = geometry.get("type")
            coords = geometry.get("coordinates") or []
            lines = [coords] if kind == "LineString" else coords if kind == "MultiLineString" else []
            for line in lines:
                points = [(float(c[0]), float(c[1])) for c in line if len(c) >= 2]
                if len(points) >= 2:
                    self._features.append((feature.get("properties") or {}, points))
        all_points = [point for _, line in self._features for point in line]
        if all_points:
            self._bounds = (min(x for x, _ in all_points), min(y for _, y in all_points),
                            max(x for x, _ in all_points), max(y for _, y in all_points))
        else:
            self._bounds = None
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor("#f7f9fc"))
        p.setPen(QColor("#334155"))
        p.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
        p.drawText(12, 20, "Предпросмотр дорожной сети OpenStreetMap")
        if not self._features or not self._bounds:
            p.setPen(QColor("#64748b"))
            p.setFont(QFont("Segoe UI", 9))
            p.drawText(14, 48, "Импортируйте дороги OSM или откройте GeoJSON, чтобы увидеть карту.")
            p.drawText(14, 68, "© OpenStreetMap contributors")
            return
        minx, miny, maxx, maxy = self._bounds
        dx, dy = max(maxx-minx, 1e-9), max(maxy-miny, 1e-9)
        pad, top_pad = 16.0, 34.0
        scale = min((self.width()-2*pad)/dx, (self.height()-top_pad-pad)/dy)
        draw_w, draw_h = dx*scale, dy*scale
        ox = (self.width()-draw_w)/2
        oy = top_pad + (self.height()-top_pad-draw_h)/2
        colors = {"motorway":"#d97706","trunk":"#ea580c","primary":"#ef4444",
                  "secondary":"#f59e0b","tertiary":"#facc15","residential":"#94a3b8",
                  "service":"#cbd5e1","unclassified":"#cbd5e1"}
        for props, line in self._features:
            kind = str(props.get("highway", "unclassified"))
            color = QColor(colors.get(kind, "#cbd5e1"))
            width = 3.2 if kind in ("motorway", "trunk", "primary") else 2.2 if kind in ("secondary", "tertiary") else 1.3
            p.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            points = [QPointF(ox+(x-minx)*scale, oy+(maxy-y)*scale) for x,y in line]
            for a,b in zip(points, points[1:]):
                p.drawLine(a,b)
        p.setPen(QColor("#475569"))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(12, self.height()-8, f"Линейных сегментов: {len(self._features)} · © OpenStreetMap contributors")
