"""The Growcube integration."""
import asyncio
import logging
import voluptuous as vol
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_HOST, Platform
from homeassistant import config_entries
from .coordinator import GrowcubeDataCoordinator
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry

_LOGGER = logging.getLogger(__name__)

from .const import DOMAIN
from .frontend import async_setup_frontend
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TEXT,
    Platform.TIME,
]


async def async_migrate_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Migrate old config entries."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(entry, version=2)
        return True

    return entry.version == 2


async def async_setup_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Set up the Growcube entry."""
    hass.data.setdefault(DOMAIN, {})

    host_name = entry.data[CONF_HOST]
    data_coordinator = GrowcubeDataCoordinator(host_name, hass, entry)
    hass.data[DOMAIN][entry.entry_id] = data_coordinator
    try:
        connected, error = await data_coordinator.connect()
        if not connected:
            _LOGGER.error(
                "Unable to connect to %s: %s",
                host_name,
                error
            )
            data_coordinator.start_reconnect(
                f"GrowCube at {host_name} is unavailable: {error}. Retrying in 10 seconds."
            )

    except asyncio.TimeoutError:
        _LOGGER.error(
            "Connection to %s timed out",
            host_name
        )
        data_coordinator.start_reconnect(
            f"GrowCube at {host_name} timed out. Retrying in 10 seconds."
        )
    except OSError:
        _LOGGER.error(
            "Unable to connect to host %s",
            host_name
        )
        data_coordinator.start_reconnect(
            f"GrowCube at {host_name} is unavailable. Retrying in 10 seconds."
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_services(hass)
    await async_setup_frontend(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: config_entries.ConfigEntry) -> bool:
    """Unload the Growcube entry."""
    client = hass.data[DOMAIN][entry.entry_id]
    client.disconnect()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
