"""Data models for the GrowCube integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from enum import IntEnum


class WateringMode(IntEnum):
    """GrowCube watering modes used by command 49."""

    DISABLED = 0
    REPEATING = 1
    SMART = 2


@dataclass(slots=True)
class GrowcubeHistoryPoint:
    """One hourly moisture history point."""

    channel: int
    timestamp: datetime
    moisture: int


@dataclass(slots=True)
class GrowcubeWateringEvent:
    """One stored watering event."""

    channel: int
    timestamp: datetime
    amount_ml: int | None = None


@dataclass(slots=True)
class GrowcubeChannelConfig:
    """Home Assistant side channel watering settings."""

    configured: bool = False
    plant_name: str = ""
    photo_url: str = ""
    type_category: str = ""
    type_description: str = ""
    temp_min: int = 0
    temp_max: int = 0
    air_humidity_min: int = 0
    air_humidity_max: int = 0
    mode: WateringMode = WateringMode.DISABLED
    manual_duration_seconds: int = 7
    manual_amount_ml: int = 50
    duration_seconds: int = 7
    amount_ml: int = 50
    interval_hours: int = 24
    first_watering_time: time | None = None
    timed_watering_anchor: datetime | None = None
    smart_min_moisture: int = 20
    smart_max_moisture: int = 60
    smart_daytime_watering: bool = True
    smart_amount_ml: int = 0
    smart_watering_count: int = 0


@dataclass(slots=True)
class GrowcubeChannelState:
    """Per-channel GrowCube state."""

    moisture: int | None = None
    pump_open: bool = False
    sensor_fault: bool = False
    sensor_disconnected: bool = False
    watering_issue: bool = False
    watering_locked: bool = False
    outlet_blocked: bool = False
    outlet_locked: bool = False
    last_watering: datetime | None = None
    history_loading: bool = False
    history_complete: bool = False
    watering_events_complete: bool = False
    history: list[GrowcubeHistoryPoint] = field(default_factory=list)
    watering_events: list[GrowcubeWateringEvent] = field(default_factory=list)
    config: GrowcubeChannelConfig = field(default_factory=GrowcubeChannelConfig)


@dataclass(slots=True)
class GrowcubeTankConfig:
    """Home Assistant side tank settings."""

    capacity_ml: int = 1500


@dataclass(slots=True)
class GrowcubeTankState:
    """Tracked tank state."""

    remaining_ml: int = 1500
    used_ml: int = 0
    last_filled: datetime | None = None


@dataclass(slots=True)
class GrowcubeTankForecast:
    """Firmware learned tank usage forecast from command 54."""

    known: bool = False
    flags: int = 0
    valid_days: int = 0
    confidence: int = 0
    smart_daily_x10: int = 0
    manual_daily_x10: int = 0
    unknown_daily_x10: int = 0
    smart_events: int = 0
    manual_events: int = 0
    unknown_events: int = 0
    today_smart_ml: int = 0
    today_manual_ml: int = 0
    today_unknown_ml: int = 0


@dataclass(slots=True)
class GrowcubeData:
    """Full GrowCube state snapshot."""

    connected: bool = False
    host: str = ""
    port: int = 8800
    device_id: str | None = None
    version: str | None = None
    temperature: int | None = None
    humidity: int | None = None
    water_warning: bool = False
    device_locked: bool = False
    device_lock_reason: int | None = None
    last_message: str = ""
    channels: list[GrowcubeChannelState] = field(
        default_factory=lambda: [GrowcubeChannelState() for _ in range(4)]
    )
    tank_config: GrowcubeTankConfig = field(default_factory=GrowcubeTankConfig)
    tank_state: GrowcubeTankState = field(default_factory=GrowcubeTankState)
    tank_forecast: GrowcubeTankForecast = field(default_factory=GrowcubeTankForecast)
