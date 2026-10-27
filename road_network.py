"""Road-network import utilities.

OSM is an optional source for geometry. This does not imply that 2GIS exposes
road topology through the Places API; use only data sources whose terms permit it.
"""
from __future__ import annotations
import json
import hashlib
import time
from pathlib import Path
from typing import Any
import requests

OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)

class RoadNetworkError(RuntimeError):
    pass

def validate_bbox(south: float, west: float, north: float, east: float) -> None:
    if not (-90 <= south < north <= 90):
        raise RoadNetworkError("Широты должны удовлетворять условию -90 ≤ юг < север ≤ 90.")
    if not (-180 <= west < east <= 180):
        raise RoadNetworkError("Долготы должны удовлетворять условию -180 ≤ запад < восток ≤ 180.")
    if (north - south) * (east - west) > 0.04:
        raise RoadNetworkError("Область слишком велика. Уменьшите прямоугольник импорта.")

def fetch_osm_roads(south: float, west: float, north: float, east: float,
                    timeout: int = 45) -> dict[str, Any]:
    """Fetch tagged road ways as GeoJSON-compatible features from Overpass."""
    validate_bbox(south, west, north, east)
    # Cache each requested area locally for 30 days to avoid repeated API waits.
    cache_root = Path.home() / ".traffic_signal_studio" / "osm_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    cache_key = hashlib.sha256(
        ",".join(f"{value:.5f}" for value in (south, west, north, east)).encode("ascii")
    ).hexdigest()[:24]
    cache_file = cache_root / f"{cache_key}.geojson"
    if cache_file.exists():
        try:
            if time.time() - cache_file.stat().st_mtime < 30 * 24 * 60 * 60:
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if cached.get("type") == "FeatureCollection" and cached.get("features"):
                    cached["cache_status"] = "hit"
                    return cached
        except (OSError, json.JSONDecodeError):
            pass

    query = f'''[out:json][timeout:30];(way["highway"]({south},{west},{north},{east}););out geom;'''
    errors = []
    headers = {"User-Agent": "TrafficSignalStudio/0.3 (desktop traffic analysis)"}
    # Public Overpass instances can temporarily return 429/502 or time out.
    # Retry transient failures briefly, then move to the next independent mirror.
    for endpoint in OVERPASS_ENDPOINTS:
        for attempt in range(2):
            try:
                response = requests.post(endpoint, data={"data": query}, timeout=timeout,
                                         headers=headers)
                response.raise_for_status()
                payload = response.json()
                features = []
                for element in payload.get("elements", []):
                    geometry = element.get("geometry") or []
                    if len(geometry) < 2:
                        continue
                    props = dict(element.get("tags") or {})
                    props.update({"osm_type": element.get("type", "way"), "osm_id": element.get("id")})
                    coordinates = [[p["lon"], p["lat"]] for p in geometry if "lon" in p and "lat" in p]
                    if len(coordinates) < 2:
                        continue
                    features.append({"type": "Feature", "id": f"way/{element.get('id')}",
                        "properties": props, "geometry": {"type": "LineString",
                        "coordinates": coordinates}})
                if not features:
                    raise RoadNetworkError("В выбранной области дороги не найдены.")
                result = {"type": "FeatureCollection", "name": "TrafficSignalStudio OSM road import",
                          "source": "OpenStreetMap via Overpass API", "features": features,
                          "cache_status": "miss"}
                try:
                    cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                except OSError:
                    # A cache failure should not prevent a successful map import.
                    pass
                return result
            except RoadNetworkError:
                raise
            except (requests.RequestException, ValueError, KeyError) as exc:
                errors.append(f"{endpoint} (попытка {attempt + 1}/2): {exc}")
                if attempt == 0:
                    time.sleep(1.5)
    raise RoadNetworkError(
        "Сервисы OpenStreetMap Overpass временно недоступны или перегружены. "
        "Повторите импорт позже либо откройте ранее сохранённый GeoJSON. Подробности: "
        + " | ".join(errors)
    )

def load_geojson(path: str | Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RoadNetworkError(f"Не удалось прочитать GeoJSON: {exc}") from exc
    if data.get("type") != "FeatureCollection" or not isinstance(data.get("features"), list):
        raise RoadNetworkError("Ожидается GeoJSON FeatureCollection.")
    roads = [f for f in data["features"] if f.get("geometry", {}).get("type") in ("LineString", "MultiLineString")]
    if not roads:
        raise RoadNetworkError("В GeoJSON нет линейных объектов дорожной сети.")
    return data

def save_geojson(data: dict[str, Any], path: str | Path) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def infer_intersection_approaches(geojson: dict[str, Any], latitude: float, longitude: float,
                                  tolerance_m: float = 22.0) -> dict[str, Any]:
    """Infer connected approach directions and approximate lane counts near a selected OSM node.

    OSM ways often share coordinates at junctions. This routine is intentionally conservative:
    it only trusts line vertices close to the selected point, and labels lane counts as estimates.
    """
    import math

    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise RoadNetworkError("Координаты перекрёстка находятся вне допустимого диапазона.")
    if not 5 <= tolerance_m <= 100:
        raise RoadNetworkError("Радиус поиска узла должен быть в диапазоне 5–100 м.")

    def distance_m(lon, lat):
        dy = (lat - latitude) * 111_000.0
        dx = (lon - longitude) * 111_000.0 * max(0.05, math.cos(math.radians(latitude)))
        return math.hypot(dx, dy)

    directions = {"Север": [], "Восток": [], "Юг": [], "Запад": []}
    matched = []
    arm_bearings = []
    for feature in (geojson or {}).get("features", []):
        geometry = feature.get("geometry") or {}
        kind, coords = geometry.get("type"), geometry.get("coordinates") or []
        lines = [coords] if kind == "LineString" else coords if kind == "MultiLineString" else []
        props = feature.get("properties") or {}
        for line in lines:
            for index, point in enumerate(line):
                if len(point) < 2 or distance_m(float(point[0]), float(point[1])) > tolerance_m:
                    continue
                neighbors = []
                if index > 0:
                    neighbors.append(line[index - 1])
                if index + 1 < len(line):
                    neighbors.append(line[index + 1])
                for neighbor in neighbors:
                    dx = (float(neighbor[0]) - longitude) * math.cos(math.radians(latitude))
                    dy = float(neighbor[1]) - latitude
                    if math.hypot(dx, dy) < 1e-9:
                        continue
                    angle = math.degrees(math.atan2(dx, dy)) % 360.0
                    arm_bearings.append(angle)
                    if angle >= 315 or angle < 45:
                        side = "Север"
                    elif angle < 135:
                        side = "Восток"
                    elif angle < 225:
                        side = "Юг"
                    else:
                        side = "Запад"
                    try:
                        lane_total = int(str(props.get("lanes", "")).split(";")[0])
                    except (TypeError, ValueError):
                        lane_total = 0
                    try:
                        directional = int(str(props.get("lanes:forward", "")).split(";")[0])
                    except (TypeError, ValueError):
                        directional = 0
                    try:
                        backward = int(str(props.get("lanes:backward", "")).split(";")[0])
                    except (TypeError, ValueError):
                        backward = 0
                    if directional > 0 or backward > 0:
                        estimate = max(directional, backward, 1)
                    elif lane_total > 0:
                        estimate = max(1, math.ceil(lane_total / 2))
                    else:
                        estimate = 1
                    record = {"approach": side, "lanes": min(8, estimate),
                              "highway": props.get("highway", "unknown"),
                              "name": props.get("name", ""),
                              "osm_id": props.get("osm_id")}
                    directions[side].append(record)
                    matched.append(record)

    # Deduplicate repeated geometry vertices from the same OSM way and approach.
    approaches = {}
    for side, records in directions.items():
        unique = {}
        for record in records:
            key = (record.get("osm_id"), record.get("name"), record.get("highway"))
            unique[key] = record
        if unique:
            records = list(unique.values())
            approaches[side] = {
                "lanes": max(record["lanes"] for record in records),
                "roads": records,
                "names": sorted({record["name"] for record in records if record["name"]}),
            }
    if len(approaches) < 2:
        raise RoadNetworkError(
            "Не удалось надёжно определить минимум два подхода рядом с выбранной точкой. "
            "Увеличьте масштаб, выберите общий узел дорог или загрузите более подробные данные OSM."
        )
    # Estimate the number of distinct arms by clustering nearby bearings.
    clustered_bearings = []
    for bearing in sorted(arm_bearings):
        if not any(min(abs(bearing - existing), 360 - abs(bearing - existing)) <= 25
                   for existing in clustered_bearings):
            clustered_bearings.append(bearing)
    if len(clustered_bearings) > 1 and clustered_bearings[0] + 360 - clustered_bearings[-1] <= 25:
        clustered_bearings.pop()
    return {
        "latitude": latitude,
        "longitude": longitude,
        "approaches": approaches,
        "detected_approach_count": len(approaches),
        "detected_arm_count": len(clustered_bearings),
        "warnings": [
            "Число полос — оценка по тегам OSM; при отсутствии тегов принято 1.",
            "Данные OSM не задают реальные разрешённые повороты, стоп-линии, фазы или интенсивность.",
            "Текущий схематический редактор имеет четыре стороны; отсутствующие подходы показаны как 1 полоса и требуют ручной проверки."
        ],
    }
