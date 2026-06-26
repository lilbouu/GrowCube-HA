"""GrowCube online plant catalog helpers."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

CATALOG_HOSTS = ("https://api.growcube.cc", "http://api.growcube.cc")
CATALOG_LIMIT = 40


async def async_search_plants(hass: HomeAssistant, query: str) -> list[dict[str, Any]]:
    """Search the GrowCube online plant catalog."""

    query = query.strip()
    if len(query) < 2:
        return []

    data = await _async_fetch_catalog(hass, f"/api/en/plants/name/{quote(query, safe='')}")
    plants = data.get("plants")
    if not isinstance(plants, list):
        return []

    return [_plant_from_api(plant) for plant in plants[:CATALOG_LIMIT] if isinstance(plant, dict)]


async def _async_fetch_catalog(hass: HomeAssistant, path: str) -> dict[str, Any]:
    session = aiohttp_client.async_get_clientsession(hass)
    last_error: Exception | None = None
    for host in CATALOG_HOSTS:
        try:
            async with session.get(
                f"{host}{path}",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "GrowCube/4.1",
                },
                timeout=15,
            ) as response:
                response.raise_for_status()
                data = await response.json(content_type=None)
                return data if isinstance(data, dict) else {}
        except (ClientError, TimeoutError) as err:
            last_error = err
    if last_error:
        raise last_error
    return {}


def _plant_from_api(plant: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _as_int(plant.get("id")),
        "name": _as_str(plant.get("name")),
        "display_name": _as_str(plant.get("display_name")),
        "category": _as_str(plant.get("category")),
        "description": _as_str(plant.get("description")),
        "image_url": _as_str(plant.get("image")),
        "moisture_min": _clamp(_as_int(plant.get("min_soil_moist"), 30), 0, 100),
        "moisture_max": _clamp(_as_int(plant.get("max_soil_moist"), 60), 0, 100),
        "temp_min": _as_int(plant.get("min_temp")),
        "temp_max": _as_int(plant.get("max_temp")),
        "air_humidity_min": _as_int(plant.get("min_env_humid")),
        "air_humidity_max": _as_int(plant.get("max_env_humid")),
    }


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: int, min_value: int, max_value: int) -> int:
    return min(max_value, max(min_value, value))
