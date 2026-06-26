"""Frontend helpers for the GrowCube integration."""

from __future__ import annotations

from pathlib import Path
import logging

from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .api import (
    GrowcubeDashboardView,
    GrowcubeHistoryView,
    GrowcubePlantSearchView,
    GrowcubeProvisionSessionView,
    GrowcubeProvisionStatusView,
)

_LOGGER = logging.getLogger(__name__)

CARD_URL = f"/api/{DOMAIN}/growcube-card.js"
CARD_RESOURCE_URL = f"{CARD_URL}?v=20260625-global-device-switcher"
CARD_PATH = Path(__file__).parent / "www" / "growcube-card.js"
PROVISION_URL = f"/api/{DOMAIN}/provision/index.html"
PROVISION_PATH = Path(__file__).parent / "www" / "provision"
IMAGE_URL = f"/api/{DOMAIN}/images"
IMAGE_PATH = Path(__file__).parent / "www" / "images"


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Serve and register the GrowCube Lovelace card."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get("frontend_registered"):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=False),
                StaticPathConfig(PROVISION_URL, str(PROVISION_PATH / "index.html"), cache_headers=False),
                StaticPathConfig(f"/api/{DOMAIN}/provision", str(PROVISION_PATH), cache_headers=False),
                StaticPathConfig(IMAGE_URL, str(IMAGE_PATH), cache_headers=False),
            ]
        )
        hass.http.register_view(GrowcubePlantSearchView(hass))
        hass.http.register_view(GrowcubeDashboardView(hass))
        hass.http.register_view(GrowcubeHistoryView(hass))
        hass.http.register_view(GrowcubeProvisionSessionView(hass))
        hass.http.register_view(GrowcubeProvisionStatusView(hass))
        domain_data["frontend_registered"] = True

    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        _LOGGER.debug("Lovelace is not loaded yet; skipping GrowCube card resource registration")
        return

    resource_collection = lovelace_data.resources
    if not hasattr(resource_collection, "async_create_item"):
        _LOGGER.debug("Lovelace is not in storage mode; skipping GrowCube card resource registration")
        return

    await resource_collection.async_load()
    resource_url = CARD_RESOURCE_URL.split("?", maxsplit=1)[0]
    for item in resource_collection.async_items() or []:
        url = item.get(CONF_URL, "")
        if url.split("?", maxsplit=1)[0] == resource_url:
            if url != CARD_RESOURCE_URL and item.get("id"):
                await resource_collection.async_update_item(
                    item["id"],
                    {CONF_URL: CARD_RESOURCE_URL},
                )
            return

    await resource_collection.async_create_item(
        {
            "res_type": "module",
            CONF_URL: CARD_RESOURCE_URL,
        }
    )
