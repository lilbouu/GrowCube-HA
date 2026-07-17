from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .coordinator import GrowcubeDataCoordinator
from .const import CHANNEL_ID, CHANNEL_NAME, DOMAIN
import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]

    buttons = [
        WaterPlantButton(coordinator, 0),
        WaterPlantButton(coordinator, 1),
        WaterPlantButton(coordinator, 2),
        WaterPlantButton(coordinator, 3),
        StopWateringButton(coordinator, 0),
        StopWateringButton(coordinator, 1),
        StopWateringButton(coordinator, 2),
        StopWateringButton(coordinator, 3),
        SaveScheduleButton(coordinator, 0),
        SaveScheduleButton(coordinator, 1),
        SaveScheduleButton(coordinator, 2),
        SaveScheduleButton(coordinator, 3),
        AddPlantButton(coordinator, 0),
        AddPlantButton(coordinator, 1),
        AddPlantButton(coordinator, 2),
        AddPlantButton(coordinator, 3),
        ResetPlantButton(coordinator, 0),
        ResetPlantButton(coordinator, 1),
        ResetPlantButton(coordinator, 2),
        ResetPlantButton(coordinator, 3),
        LoadHistoryButton(coordinator, 0),
        LoadHistoryButton(coordinator, 1),
        LoadHistoryButton(coordinator, 2),
        LoadHistoryButton(coordinator, 3),
        MarkTankFullButton(coordinator),
        ResetNetworkButton(coordinator),
        CheckFirmwareButton(coordinator),
        UpdateFirmwareButton(coordinator),
    ]

    async_add_entities(buttons)


class WaterPlantButton(CoordinatorEntity[GrowcubeDataCoordinator], ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Water plant {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.data.device_id}_water_plant_{CHANNEL_ID[channel]}"
        self._attr_device_info = coordinator.data.device_info

    @property
    def icon(self) -> str:
        return "mdi:watering-can"

    async def async_press(self) -> None:
        await self.coordinator.water_plant(self._channel)


class StopWateringButton(CoordinatorEntity[GrowcubeDataCoordinator], ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Stop watering {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.data.device_id}_stop_watering_{CHANNEL_ID[channel]}"
        self._attr_device_info = coordinator.data.device_info

    @property
    def icon(self) -> str:
        return "mdi:water-off"

    async def async_press(self) -> None:
        await self.coordinator.stop_watering(self._channel)


class SaveScheduleButton(CoordinatorEntity[GrowcubeDataCoordinator], ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Save watering {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.data.device_id}_save_schedule_{CHANNEL_ID[channel]}"
        self._attr_device_info = coordinator.data.device_info

    @property
    def icon(self) -> str:
        return "mdi:content-save"

    async def async_press(self) -> None:
        await self.coordinator.apply_watering_settings(self._channel)


class AddPlantButton(CoordinatorEntity[GrowcubeDataCoordinator], ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Add plant {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.data.device_id}_add_plant_{CHANNEL_ID[channel]}"
        self._attr_device_info = coordinator.data.device_info

    @property
    def icon(self) -> str:
        return "mdi:plus-circle-outline"

    async def async_press(self) -> None:
        await self.coordinator.async_add_plant(self._channel)


class ResetPlantButton(CoordinatorEntity[GrowcubeDataCoordinator], ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Reset plant {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.data.device_id}_reset_plant_{CHANNEL_ID[channel]}"
        self._attr_device_info = coordinator.data.device_info

    @property
    def icon(self) -> str:
        return "mdi:delete-outline"

    async def async_press(self) -> None:
        await self.coordinator.async_reset_plant(self._channel)


class LoadHistoryButton(CoordinatorEntity[GrowcubeDataCoordinator], ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Load history {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.data.device_id}_load_history_{CHANNEL_ID[channel]}"
        self._attr_device_info = coordinator.data.device_info

    @property
    def icon(self) -> str:
        return "mdi:chart-line"

    async def async_press(self) -> None:
        _LOGGER.warning("GrowCube history button pressed for channel %s", self._channel)
        await self.coordinator.async_request_history(self._channel)


class MarkTankFullButton(CoordinatorEntity[GrowcubeDataCoordinator], ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowcubeDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Mark tank full"
        self._attr_unique_id = f"{coordinator.data.device_id}_mark_tank_full"
        self._attr_device_info = coordinator.data.device_info

    @property
    def icon(self) -> str:
        return "mdi:cup-water"

    async def async_press(self) -> None:
        await self.coordinator.async_mark_tank_full()


class ResetNetworkButton(CoordinatorEntity[GrowcubeDataCoordinator], ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowcubeDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Reset network"
        self._attr_unique_id = f"{coordinator.data.device_id}_reset_network"
        self._attr_device_info = coordinator.data.device_info

    @property
    def icon(self) -> str:
        return "mdi:wifi-refresh"

    async def async_press(self) -> None:
        await self.coordinator.async_reset_network()


class CheckFirmwareButton(CoordinatorEntity[GrowcubeDataCoordinator], ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowcubeDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Check firmware"
        self._attr_unique_id = f"{coordinator.data.device_id}_check_firmware"
        self._attr_device_info = coordinator.data.device_info

    @property
    def icon(self) -> str:
        return "mdi:cloud-search-outline"

    async def async_press(self) -> None:
        await self.coordinator.async_check_firmware_update()


class UpdateFirmwareButton(CoordinatorEntity[GrowcubeDataCoordinator], ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GrowcubeDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_name = "Update firmware"
        self._attr_unique_id = f"{coordinator.data.device_id}_update_firmware"
        self._attr_device_info = coordinator.data.device_info

    @property
    def icon(self) -> str:
        return "mdi:update"

    async def async_press(self) -> None:
        await self.coordinator.async_update_firmware()
