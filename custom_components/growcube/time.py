"""Support for GrowCube time entities."""

from __future__ import annotations

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CHANNEL_ID, CHANNEL_NAME, DOMAIN
from .coordinator import GrowcubeDataCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GrowCube time entities."""

    coordinator: GrowcubeDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([GrowcubeFirstWateringTime(coordinator, channel) for channel in range(len(CHANNEL_NAME))])


class GrowcubeFirstWateringTime(CoordinatorEntity[GrowcubeDataCoordinator], RestoreEntity, TimeEntity):
    """Preferred first watering time for one channel."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:clock-start"

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"First watering time {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.host}_first_watering_time_{CHANNEL_ID[channel]}"

    @property
    def device_info(self):
        return self.coordinator.device_info

    @property
    def native_value(self) -> time | None:
        return self.coordinator.data.channels[self._channel].config.first_watering_time

    async def async_added_to_hass(self) -> None:
        """Restore the preferred first watering time after Home Assistant restarts."""

        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is None or last_state.state in ("unknown", "unavailable"):
            return
        try:
            restored = time.fromisoformat(last_state.state)
        except ValueError:
            return
        await self.coordinator.async_set_first_watering_time(self._channel, restored)

    async def async_set_value(self, value: time) -> None:
        await self.coordinator.async_set_first_watering_time(self._channel, value)
