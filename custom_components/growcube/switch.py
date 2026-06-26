"""Support for GrowCube switch entities."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CHANNEL_ID, CHANNEL_NAME, DOMAIN
from .coordinator import GrowcubeDataCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GrowCube switch entities."""

    coordinator: GrowcubeDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [GrowcubeSmartDaytimeWateringSwitch(coordinator, channel) for channel in range(len(CHANNEL_NAME))]
    )


class GrowcubeSmartDaytimeWateringSwitch(CoordinatorEntity[GrowcubeDataCoordinator], SwitchEntity):
    """Allow smart watering during daytime."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:white-balance-sunny"

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Daytime watering {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.host}_smart_daytime_watering_{CHANNEL_ID[channel]}"

    @property
    def device_info(self):
        return self.coordinator.device_info

    @property
    def is_on(self) -> bool:
        return self.coordinator.data.channels[self._channel].config.smart_daytime_watering

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_smart_daytime_watering(self._channel, True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_smart_daytime_watering(self._channel, False)
