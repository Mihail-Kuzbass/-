"""Simple, attributed preview of imported OpenStreetMap road geometry."""
from collections import Counter
from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import QWidget


class OSMRoadPreview(QWidget):
    coordinateSelected = Signal(float, float, bool)
    """Render imported GeoJSON LineString/MultiLineString roads in a local view."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(260)
        self.setMinimumWidth(420)
        self._features = []
        self._bounds = None
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._drag_pos = None
        self._intersections = []
        self._selected = None
        self.setMouseTracking(True)
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
        node_counts = Counter((round(x, 7), round(y, 7))
                              for _, line in self._features for x, y in line)
        self._intersections = [(x, y) for (x, y), count in node_counts.items() if count >= 2]
        self._zoom, self._pan_x, self._pan_y = 1.0, 0.0, 0.0
        self._selected = None
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
        scale = min((self.width()-2*pad)/dx, (self.height()-top_pad-pad)/dy) * self._zoom
        draw_w, draw_h = dx*scale, dy*scale
        ox = (self.width()-draw_w)/2 + self._pan_x
        oy = top_pad + (self.height()-top_pad-draw_h)/2 + self._pan_y
        self._transform = (minx, maxy, scale, ox, oy)
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
        if self._selected:
            sx, sy = self._selected
            point = QPointF(ox + (sx-minx)*scale, oy + (maxy-sy)*scale)
            p.setPen(QPen(QColor("#ffffff"), 5))
            p.setBrush(QColor("#2563eb"))
            p.drawEllipse(point, 7, 7)
        p.setPen(QColor("#475569"))
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(12, self.height()-8,
                   f"Линейных сегментов: {len(self._features)} · Масштаб: {self._zoom:.1f}× · Колёсико — масштаб, перетаскивание — перемещение · © OpenStreetMap contributors")

    def wheelEvent(self, event):
        if not self._bounds:
            return
        old_zoom = self._zoom
        factor = 1.2 if event.angleDelta().y() > 0 else 1 / 1.2
        self._zoom = max(0.5, min(20.0, self._zoom * factor))
        ratio = self._zoom / old_zoom
        pos = event.position()
        self._pan_x = pos.x() - (pos.x() - self._pan_x) * ratio
        self._pan_y = pos.y() - (pos.y() - self._pan_y) * ratio
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            delta = pos - self._drag_pos
            self._pan_x += delta.x()
            self._pan_y += delta.y()
            self._drag_pos = pos
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_pos = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if not self._bounds or not hasattr(self, "_transform"):
            return
        minx, maxy, scale, ox, oy = self._transform
        px, py = event.position().x(), event.position().y()
        lon = minx + (px-ox)/scale
        lat = maxy - (py-oy)/scale
        candidates = self._intersections or [(x, y) for _, line in self._features for x, y in line]
        if not candidates:
            return
        chosen = min(candidates, key=lambda point: (point[0]-lon)**2 + (point[1]-lat)**2)
        screen_x = ox + (chosen[0]-minx)*scale
        screen_y = oy + (maxy-chosen[1])*scale
        if (screen_x-px)**2 + (screen_y-py)**2 > 45**2:
            return
        self._selected = chosen
        self.coordinateSelected.emit(chosen[1], chosen[0], bool(self._intersections))
        self.update()

    def reset_view(self):
        self._zoom, self._pan_x, self._pan_y = 1.0, 0.0, 0.0
        self.update()
