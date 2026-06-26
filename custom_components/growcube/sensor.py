"""Support for Growcube sensors."""
from datetime import datetime, timedelta

from homeassistant.const import PERCENTAGE, UnitOfTemperature, Platform
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.core import callback, HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.util import dt as dt_util
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, CHANNEL_ID, CHANNEL_NAME
from .models import WateringMode
import logging

from .coordinator import GrowcubeDataCoordinator

TANK_UNUSABLE_RESERVE_ML = 300

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(minutes=1)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up the Growcube sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([TemperatureSensor(coordinator),
                        HumiditySensor(coordinator),
                        TankRemainingSensor(coordinator),
                        TankLevelSensor(coordinator),
                        TankUsedSensor(coordinator),
                        TankDaysLeftSensor(coordinator),
                        MoistureSensor(coordinator, 0),
                        MoistureSensor(coordinator, 1),
                        MoistureSensor(coordinator, 2),
                        MoistureSensor(coordinator, 3),
                        LastWateringSensor(coordinator, 0),
                        LastWateringSensor(coordinator, 1),
                        LastWateringSensor(coordinator, 2),
                        LastWateringSensor(coordinator, 3),
                        HistoryCountSensor(coordinator, 0),
                        HistoryCountSensor(coordinator, 1),
                        HistoryCountSensor(coordinator, 2),
                        HistoryCountSensor(coordinator, 3),
                        NextWateringSensor(coordinator, 0),
                        NextWateringSensor(coordinator, 1),
                        NextWateringSensor(coordinator, 2),
                        NextWateringSensor(coordinator, 3)])


class TemperatureSensor(CoordinatorEntity[GrowcubeDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "temperature"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_device_class = SensorDeviceClass.TEMPERATURE

    def __init__(self, coordinator: GrowcubeDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.device_id}_temperature"
        self._attr_device_info = coordinator.data.device_info

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.temperature


class HumiditySensor(CoordinatorEntity[GrowcubeDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "humidity"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.HUMIDITY

    def __init__(self, coordinator: GrowcubeDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.device_id}_humidity"
        self._attr_device_info = coordinator.data.device_info

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.humidity


class TankRemainingSensor(CoordinatorEntity[GrowcubeDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Tank remaining"
    _attr_native_unit_of_measurement = "mL"
    _attr_icon = "mdi:cup-water"

    def __init__(self, coordinator: GrowcubeDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.device_id}_tank_remaining"
        self._attr_device_info = coordinator.data.device_info

    @property
    def native_value(self) -> int:
        return self.coordinator.data.tank_state.remaining_ml


class TankLevelSensor(CoordinatorEntity[GrowcubeDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Tank level"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:water-percent"

    def __init__(self, coordinator: GrowcubeDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.device_id}_tank_level"
        self._attr_device_info = coordinator.data.device_info

    @property
    def native_value(self) -> int:
        capacity = max(1, self.coordinator.data.tank_config.capacity_ml)
        return round(self.coordinator.data.tank_state.remaining_ml / capacity * 100)


class TankUsedSensor(CoordinatorEntity[GrowcubeDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Tank used"
    _attr_native_unit_of_measurement = "mL"
    _attr_icon = "mdi:water-minus"

    def __init__(self, coordinator: GrowcubeDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.device_id}_tank_used"
        self._attr_device_info = coordinator.data.device_info

    @property
    def native_value(self) -> int:
        return self.coordinator.data.tank_state.used_ml


class TankDaysLeftSensor(CoordinatorEntity[GrowcubeDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Tank days left"
    _attr_native_unit_of_measurement = "d"
    _attr_icon = "mdi:calendar-range"

    def __init__(self, coordinator: GrowcubeDataCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.data.device_id}_tank_days_left"
        self._attr_device_info = coordinator.data.device_info

    @property
    def native_value(self) -> float | None:
        state = self.coordinator.data.tank_state
        daily_usage = self.coordinator.estimated_daily_usage_ml()
        if daily_usage <= 0:
            return None
        usable_remaining_ml = max(0, state.remaining_ml - TANK_UNUSABLE_RESERVE_ML)
        return round(usable_remaining_ml / daily_usage, 1)

    @property
    def extra_state_attributes(self) -> dict[str, int | bool | float | None]:
        forecast = self.coordinator.data.tank_forecast
        daily_usage = self.coordinator.estimated_daily_usage_ml()
        remaining_ml = self.coordinator.data.tank_state.remaining_ml
        usable_remaining_ml = max(0, remaining_ml - TANK_UNUSABLE_RESERVE_ML)
        return {
            "daily_usage_ml": round(daily_usage, 1) if daily_usage > 0 else None,
            "unusable_reserve_ml": TANK_UNUSABLE_RESERVE_ML,
            "usable_remaining_ml": usable_remaining_ml,
            "firmware_forecast_known": forecast.known,
            "firmware_forecast_flags": forecast.flags,
            "firmware_forecast_valid_days": forecast.valid_days,
            "firmware_forecast_confidence": forecast.confidence,
            "firmware_smart_daily_ml": forecast.smart_daily_x10 / 10,
            "firmware_manual_daily_ml": forecast.manual_daily_x10 / 10,
            "firmware_unknown_daily_ml": forecast.unknown_daily_x10 / 10,
            "firmware_smart_events": forecast.smart_events,
            "firmware_manual_events": forecast.manual_events,
            "firmware_unknown_events": forecast.unknown_events,
            "firmware_today_smart_ml": forecast.today_smart_ml,
            "firmware_today_manual_ml": forecast.today_manual_ml,
            "firmware_today_unknown_ml": forecast.today_unknown_ml,
        }


class MoistureSensor(CoordinatorEntity[GrowcubeDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_device_class = SensorDeviceClass.MOISTURE
    _attr_icon = "mdi:cup-water"

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Moisture {CHANNEL_NAME[self._channel]}"
        self._attr_unique_id = f"{coordinator.data.device_id}_moisture_{CHANNEL_ID[self._channel]}"
        self._attr_device_info = coordinator.data.device_info

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.moisture[self._channel]


class LastWateringSensor(CoordinatorEntity[GrowcubeDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:water-clock"

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Last watering {CHANNEL_NAME[self._channel]}"
        self._attr_unique_id = f"{coordinator.data.device_id}_last_watering_{CHANNEL_ID[self._channel]}"
        self._attr_device_info = coordinator.data.device_info

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.data.channels[self._channel].last_watering


class HistoryCountSensor(CoordinatorEntity[GrowcubeDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_icon = "mdi:chart-timeline-variant"

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"History count {CHANNEL_NAME[self._channel]}"
        self._attr_unique_id = f"{coordinator.data.device_id}_history_count_{CHANNEL_ID[self._channel]}"
        self._attr_device_info = coordinator.data.device_info

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.channels[self._channel].history)

    @property
    def extra_state_attributes(self) -> dict[str, bool | int | str | None | list[dict[str, int | str | None]] | list[str]]:
        channel = self.coordinator.data.channels[self._channel]
        return {
            "history_loading": channel.history_loading,
            "history_complete": channel.history_complete,
            "watering_events_complete": channel.watering_events_complete,
            "history_points": len(channel.history),
            "type_category": channel.config.type_category,
            "type_description": channel.config.type_description,
            "temp_min": channel.config.temp_min,
            "temp_max": channel.config.temp_max,
            "air_humidity_min": channel.config.air_humidity_min,
            "air_humidity_max": channel.config.air_humidity_max,
            "first_history": channel.history[0].timestamp.isoformat() if channel.history else None,
            "last_history": channel.history[-1].timestamp.isoformat() if channel.history else None,
            "history": [
                {
                    "timestamp": point.timestamp.isoformat(),
                    "moisture": point.moisture,
                }
                for point in channel.history
            ],
            "watering_events": [
                {
                    "timestamp": event.timestamp.isoformat(),
                    "amount_ml": event.amount_ml,
                }
                for event in channel.watering_events
            ],
        }


class NextWateringSensor(CoordinatorEntity[GrowcubeDataCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-clock"
    _attr_should_poll = True

    def __init__(self, coordinator: GrowcubeDataCoordinator, channel: int) -> None:
        super().__init__(coordinator)
        self._channel = channel
        self._attr_name = f"Next watering {CHANNEL_NAME[self._channel]}"
        self._attr_unique_id = f"{coordinator.data.device_id}_next_watering_{CHANNEL_ID[self._channel]}"
        self._attr_device_info = coordinator.data.device_info

    @property
    def should_poll(self) -> bool:
        return True

    async def async_update(self) -> None:
        """Refresh the computed timestamp without refreshing the coordinator."""

    @property
    def native_value(self) -> datetime | None:
        config = self.coordinator.data.channels[self._channel].config
        if config.mode != WateringMode.REPEATING or config.first_watering_time is None:
            return None

        next_watering = config.timed_watering_anchor
        if next_watering is None:
            now = dt_util.now()
            next_watering = datetime.combine(
                now.date(),
                config.first_watering_time,
                tzinfo=dt_util.DEFAULT_TIME_ZONE,
            )
        elif next_watering.tzinfo is None:
            next_watering = next_watering.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        else:
            next_watering = next_watering.astimezone(dt_util.DEFAULT_TIME_ZONE)

        now = dt_util.now()
        interval = timedelta(hours=max(config.interval_hours, 1))
        while next_watering <= now:
            next_watering += interval
        return next_watering
