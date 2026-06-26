"""Support for GrowCube number entities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CHANNEL_ID, CHANNEL_NAME, DOMAIN
from .coordinator import GrowcubeDataCoordinator


@dataclass(frozen=True, kw_only=True)
class GrowcubeNumberDescription(NumberEntityDescription):
    """GrowCube number description."""

    value_fn: Callable[[GrowcubeDataCoordinator, int], int]
    set_fn: Callable[[GrowcubeDataCoordinator, int, int], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class GrowcubeTankNumberDescription(NumberEntityDescription):
    """GrowCube tank number description."""

    value_fn: Callable[[GrowcubeDataCoordinator], int]
    set_fn: Callable[[GrowcubeDataCoordinator, int], Awaitable[None]]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up GrowCube number entities."""

    coordinator: GrowcubeDataCoordinator = hass.data[DOMAIN][entry.entry_id]
    descriptions = [
        GrowcubeNumberDescription(
            key="manual_duration_seconds",
            name="Manual watering amount",
            native_min_value=30,
            native_max_value=150,
            native_step=10,
            native_unit_of_measurement="mL",
            mode=NumberMode.BOX,
            icon="mdi:watering-can",
            value_fn=lambda coordinator, channel: coordinator.data.channels[channel].config.manual_amount_ml,
            set_fn=_set_manual_duration,
        ),
        GrowcubeNumberDescription(
            key="duration_seconds",
            name="Watering amount",
            native_min_value=10,
            native_max_value=500,
            native_step=10,
            native_unit_of_measurement="mL",
            mode=NumberMode.BOX,
            icon="mdi:timer-outline",
            value_fn=lambda coordinator, channel: coordinator.data.channels[channel].config.amount_ml,
            set_fn=_set_duration,
        ),
        GrowcubeNumberDescription(
            key="interval_hours",
            name="Watering interval",
            native_min_value=1,
            native_max_value=240,
            native_step=1,
            native_unit_of_measurement=UnitOfTime.HOURS,
            mode=NumberMode.BOX,
            icon="mdi:calendar-clock",
            value_fn=lambda coordinator, channel: coordinator.data.channels[channel].config.interval_hours,
            set_fn=_set_interval,
        ),
        GrowcubeNumberDescription(
            key="smart_min_moisture",
            name="Minimum moisture",
            native_min_value=1,
            native_max_value=99,
            native_step=1,
            native_unit_of_measurement="%",
            mode=NumberMode.BOX,
            icon="mdi:water-percent",
            value_fn=lambda coordinator, channel: coordinator.data.channels[channel].config.smart_min_moisture,
            set_fn=_set_smart_min_moisture,
        ),
        GrowcubeNumberDescription(
            key="smart_max_moisture",
            name="Maximum moisture",
            native_min_value=1,
            native_max_value=99,
            native_step=1,
            native_unit_of_measurement="%",
            mode=NumberMode.BOX,
            icon="mdi:water-percent",
            value_fn=lambda coordinator, channel: coordinator.data.channels[channel].config.smart_max_moisture,
            set_fn=_set_smart_max_moisture,
        ),
    ]
    async_add_entities(
        [
            GrowcubeChannelNumber(coordinator, channel, description)
            for channel in range(len(CHANNEL_NAME))
            for description in descriptions
        ] + [
            GrowcubeTankNumber(
                coordinator,
                GrowcubeTankNumberDescription(
                    key="tank_capacity",
                    name="Tank capacity",
                    native_min_value=500,
                    native_max_value=50000,
                    native_step=50,
                    native_unit_of_measurement="mL",
                    mode=NumberMode.BOX,
                    icon="mdi:cup-water",
                    value_fn=lambda coordinator: coordinator.data.tank_config.capacity_ml,
                    set_fn=_set_tank_capacity,
                ),
            ),
        ]
    )


class GrowcubeChannelNumber(CoordinatorEntity[GrowcubeDataCoordinator], NumberEntity):
    """GrowCube per-channel number entity."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: GrowcubeDataCoordinator,
        channel: int,
        description: GrowcubeNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self.entity_description = description
        self._attr_name = f"{description.name} {CHANNEL_NAME[channel]}"
        self._attr_unique_id = f"{coordinator.host}_{description.key}_{CHANNEL_ID[channel]}"

    @property
    def device_info(self):
        return self.coordinator.device_info

    @property
    def native_value(self) -> int:
        return self.entity_description.value_fn(self.coordinator, self._channel)

    async def async_set_native_value(self, value: float) -> None:
        await self.entity_description.set_fn(self.coordinator, self._channel, int(value))


class GrowcubeTankNumber(CoordinatorEntity[GrowcubeDataCoordinator], NumberEntity):
    """GrowCube tank number entity."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: GrowcubeDataCoordinator,
        description: GrowcubeTankNumberDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_name = description.name
        self._attr_unique_id = f"{coordinator.host}_{description.key}"

    @property
    def device_info(self):
        return self.coordinator.device_info

    @property
    def native_value(self) -> int:
        return self.entity_description.value_fn(self.coordinator)

    async def async_set_native_value(self, value: float) -> None:
        await self.entity_description.set_fn(self.coordinator, int(value))


async def _set_duration(coordinator: GrowcubeDataCoordinator, channel: int, value: int) -> None:
    await coordinator.async_set_watering_duration(channel, value)


async def _set_interval(coordinator: GrowcubeDataCoordinator, channel: int, value: int) -> None:
    await coordinator.async_set_watering_interval(channel, value)


async def _set_manual_duration(coordinator: GrowcubeDataCoordinator, channel: int, value: int) -> None:
    await coordinator.async_set_manual_watering_duration(channel, value)


async def _set_smart_min_moisture(coordinator: GrowcubeDataCoordinator, channel: int, value: int) -> None:
    await coordinator.async_set_smart_min_moisture(channel, value)


async def _set_smart_max_moisture(coordinator: GrowcubeDataCoordinator, channel: int, value: int) -> None:
    await coordinator.async_set_smart_max_moisture(channel, value)


async def _set_tank_capacity(coordinator: GrowcubeDataCoordinator, value: int) -> None:
    await coordinator.async_set_tank_capacity(value)
