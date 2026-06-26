"""Support for GrowCube select entities."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CHANNEL_ID, CHANNEL_NAME, DOMAIN
from .coordinator import GrowcubeDataCoordinator
from .models import WateringMode

MODE_OPTIONS = {
    "Disabled": WateringMode.DISABLED,
    "Repeating": WateringMode.REPEATING,
    "Smart": WateringMode.SMART,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GrowCube select entities."""

    coordinator: GrowcubeDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GrowcubeWateringModeSelect(coordinator, channel) for channel in range(len(CHANNEL_NAME))])


class GrowcubeWateringModeSelect(CoordinatorEntity[GrowcubeDataCoordinator], SelectEntity):
    """GrowCube watering mode select."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:sprinkler-variant"
    _attr_options = list(MODE_OPTIONS)

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Watering mode {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.host}_watering_mode_{CHANNEL_ID[channel]}"

    @property
    def device_info(self):
        return self.coordinator.device_info

    @property
    def current_option(self) -> str:
        mode = self.coordinator.data.channels[self._channel].config.mode
        for option, option_mode in MODE_OPTIONS.items():
            if option_mode == mode:
                return option
        return "Disabled"

    async def async_select_option(self, option: str) -> None:
        mode = MODE_OPTIONS[option]
        await self.coordinator.async_set_watering_mode(self._channel, mode)
