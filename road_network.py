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
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            response = requests.post(endpoint, data={"data": query}, timeout=timeout,
                                     headers={"User-Agent": "TrafficSignalStudio/0.2 (desktop traffic analysis)"})
            response.raise_for_status()
            payload = response.json()
            features = []
            for element in payload.get("elements", []):
                geometry = element.get("geometry") or []
                if len(geometry) < 2:
                    continue
                props = dict(element.get("tags") or {})
                props.update({"osm_type": element.get("type", "way"), "osm_id": element.get("id")})
                features.append({"type": "Feature", "id": f"way/{element.get('id')}",
                    "properties": props, "geometry": {"type": "LineString",
                    "coordinates": [[p["lon"], p["lat"]] for p in geometry if "lon" in p and "lat" in p]}})
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
        except (requests.RequestException, ValueError, KeyError) as exc:
            errors.append(f"{endpoint}: {exc}")
    raise RoadNetworkError("Не удалось получить данные дорожной сети. " + " | ".join(errors))

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
