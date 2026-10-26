import requests

class DGisError(RuntimeError):
    pass

class DGisClient:
    """Small adapter for 2GIS Places API. Requires an API key with Places API access."""
    BASE_URL = "https://catalog.api.2gis.com/3.0/items"

    def __init__(self, api_key, timeout=15):
        if not api_key or not api_key.strip():
            raise DGisError("Не указан API-ключ 2ГИС.")
        self.api_key=api_key.strip()
        self.timeout=timeout

    def search(self, query, lat, lon, radius=1500, page_size=10):
        params={
            "key": self.api_key,
            "q": query or "перекрёсток",
            "point": f"{lon},{lat}",
            "radius": int(radius),
            "page_size": int(page_size),
            "fields": "items.point,items.address,items.address_name,items.name,items.full_name",
        }
        try:
            response=requests.get(self.BASE_URL,params=params,timeout=self.timeout)
            response.raise_for_status()
            data=response.json()
        except requests.RequestException as exc:
            raise DGisError(f"Сетевая ошибка/API 2ГИС: {exc}") from exc
        except ValueError as exc:
            raise DGisError("2ГИС вернул ответ, который не удалось прочитать как JSON.") from exc
        if data.get("meta",{}).get("code",200) >= 400:
            raise DGisError(str(data.get("meta",{})))
        return data
