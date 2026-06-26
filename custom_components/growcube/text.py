"""Support for GrowCube text entities."""

from __future__ import annotations

from homeassistant.components.text import TextEntity
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
    """Set up GrowCube text entities."""

    coordinator: GrowcubeDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            entity
            for channel in range(len(CHANNEL_NAME))
            for entity in (
                GrowcubePlantNameText(coordinator, channel),
                GrowcubePlantPhotoUrlText(coordinator, channel),
            )
        ]
    )


class GrowcubePlantNameText(CoordinatorEntity[GrowcubeDataCoordinator], RestoreEntity, TextEntity):
    """Editable plant name for one GrowCube channel."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:flower"
    _attr_native_max = 64

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Plant name {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.host}_plant_name_{CHANNEL_ID[channel]}"

    @property
    def device_info(self):
        return self.coordinator.device_info

    @property
    def native_value(self) -> str:
        name = self.coordinator.data.channels[self._channel].config.plant_name
        return name or f"Channel {CHANNEL_NAME[self._channel]}"

    async def async_added_to_hass(self) -> None:
        """Restore the plant name after Home Assistant restarts."""

        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            await self.coordinator.async_set_channel_name(self._channel, last_state.state)

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_set_channel_name(self._channel, value)


class GrowcubePlantPhotoUrlText(CoordinatorEntity[GrowcubeDataCoordinator], RestoreEntity, TextEntity):
    """Editable plant photo URL for one GrowCube channel."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:image-outline"
    _attr_native_max = 512

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Plant photo URL {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.host}_plant_photo_url_{CHANNEL_ID[channel]}"

    @property
    def device_info(self):
        return self.coordinator.device_info

    @property
    def native_value(self) -> str:
        return self.coordinator.data.channels[self._channel].config.photo_url

    async def async_added_to_hass(self) -> None:
        """Restore the plant photo URL after Home Assistant restarts."""

        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state not in ("unknown", "unavailable"):
            await self.coordinator.async_set_channel_photo_url(self._channel, last_state.state)

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_set_channel_photo_url(self._channel, value)
