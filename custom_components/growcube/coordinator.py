import asyncio
from datetime import datetime, time, timedelta, timezone
from typing import Any, Optional, List, Tuple, Callable, Mapping
from dataclasses import asdict, replace

from .client import GrowcubeClient, GrowcubeReport, Channel
from .client import (
    WaterStateGrowcubeReport,
    DeviceVersionGrowcubeReport,
    MoistureHumidityStateGrowcubeReport,
    PumpOpenGrowcubeReport,
    PumpCloseGrowcubeReport,
    CheckSensorGrowcubeReport,
    WateringExceptionGrowcubeReport,
    CheckOutletBlockedGrowcubeReport,
    CheckSensorNotConnectedGrowcubeReport,
    LockStateGrowcubeReport,
    CheckOutletLockedGrowcubeReport,
    WateringExceptionLockedGrowcubeReport,
    MoistureHistoryGrowcubeReport,
    WateringRecordGrowcubeReport,
    ExtendedWateringRecordGrowcubeReport,
    HistoryCompleteGrowcubeReport,
    TankStateGrowcubeReport,
    TankForecastGrowcubeReport,
    DelayedTimedWateringStateGrowcubeReport,
)
from .client import (
    GrowcubeCommand,
    SyncTimeCommand,
    PlantEndCommand,
    ClosePumpCommand,
    RequestHistoryCommand,
    RequestExtendedWateringHistoryCommand,
    RequestTankLevelCommand,
    RequestTankForecastCommand,
    RequestDelayedTimedWateringStateCommand,
    SetTankLevelCommand,
)
from .catalog import async_get_plant_by_id
from .protocol import scheduled_watering_payload, watering_mode_payload
from homeassistant.config_entries import ConfigEntry
from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.const import (
    STATE_UNAVAILABLE
)
import logging
import contextlib

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import CHANNEL_NAME, DOMAIN
from .firmware import check_growcube_firmware_update, download_growcube_firmware_update, upload_firmware_image
from .models import (
    GrowcubeChannelConfig,
    GrowcubeChannelState,
    GrowcubeHistoryPoint,
    GrowcubeTankConfig,
    GrowcubeTankForecast,
    GrowcubeTankState,
    GrowcubeWateringEvent,
    WateringMode,
)

_LOGGER = logging.getLogger(__name__)

CONF_CHANNEL_CONFIG = "channel_config"
CONF_TANK_CONFIG = "tank_config"
CONF_TANK_STATE = "tank_state"
CONF_WATERING_STATE = "watering_state"
WATER_AMOUNT_MIN_ML = 10
WATER_AMOUNT_MAX_ML = 500
WATER_AMOUNT_STEP_ML = 10
WATER_MANUAL_AMOUNT_MIN_ML = 30
WATER_MANUAL_AMOUNT_MAX_ML = 150
WATER_TANK_CUSTOM_MIN_ML = 500
WATER_TANK_CUSTOM_MAX_ML = 50000
SMART_MOISTURE_MIN = 1
SMART_MOISTURE_MAX = 99
DEFAULT_FIRST_WATERING_TIME = time(8, 0)
HISTORY_RETRY_CHECK_INTERVAL = timedelta(seconds=15)
HISTORY_LOADING_STALE = timedelta(seconds=45)
TIMED_HISTORY_REFRESH_GRACE = timedelta(seconds=5)
TIMED_HISTORY_REFRESH_RETRY = timedelta(seconds=15)
HISTORY_TRAILING_GAP_RETRY = timedelta(hours=1)
HISTORY_TRAILING_GAP_HOURS = 0
MAX_STORED_WATERING_EVENTS = 64
FIRMWARE_OTA_READY_DELAY = 20


from dataclasses import dataclass, field


class DelayedTimedWateringCommand(GrowcubeCommand):
    """Command 51 - schedule timed watering from a specific start epoch."""

    def __init__(self, channel: Channel, duration: int, interval: int, start_time: datetime, plant_id: int = 0):
        super().__init__(
            self.CMD_DELAYED_WATERING,
            scheduled_watering_payload(channel.value, duration, interval, start_time, plant_id),
        )

    def get_description(self) -> str:
        return f"DelayedTimedWateringCommand: {self.message}"


class DisableAutoWateringCommand(GrowcubeCommand):
    """Command 46 - disable automatic watering for a channel."""

    def __init__(self, channel: Channel):
        super().__init__(GrowcubeCommand.CMD_DISABLE_WATERING, f"{channel.value}")

    def get_description(self) -> str:
        return f"DisableAutoWateringCommand: {self.message}"


class ResetWateringModeCommand(GrowcubeCommand):
    """Command 49 mode 0 - clear watering mode for a channel."""

    def __init__(self, channel: Channel, plant_id: int = 0):
        super().__init__(GrowcubeCommand.CMD_WATER_MODE, watering_mode_payload(channel.value, 0, 0, 0, plant_id))

    def get_description(self) -> str:
        return f"ResetWateringModeCommand: {self.message}"


class TimedWateringModeCommand(GrowcubeCommand):
    """Command 49 mode 1 - repeating timed watering."""

    def __init__(self, channel: Channel, duration: int, interval: int, plant_id: int = 0):
        super().__init__(
            GrowcubeCommand.CMD_WATER_MODE,
            watering_mode_payload(channel.value, 1, duration, interval, plant_id),
        )

    def get_description(self) -> str:
        return f"TimedWateringModeCommand: {self.message}"


class SmartWateringModeCommand(GrowcubeCommand):
    """Command 49 mode 2/3 - smart watering by moisture range."""

    def __init__(
        self,
        channel: Channel,
        daytime_watering: bool,
        min_moisture: int,
        max_moisture: int,
        plant_id: int = 0,
    ):
        mode = 3 if daytime_watering else 2
        super().__init__(
            GrowcubeCommand.CMD_WATER_MODE,
            watering_mode_payload(channel.value, mode, min_moisture, max_moisture, plant_id),
        )

    def get_description(self) -> str:
        return f"SmartWateringModeCommand: {self.message}"


@dataclass
class GrowcubeData:
    """Class to hold Growcube data."""
    connected: bool = False
    temperature: Optional[int] = None
    humidity: Optional[int] = None
    moisture: List[Optional[int]] = field(default_factory=lambda: [None] * 4)
    pump_open: List[bool] = field(default_factory=lambda: [False] * 4)
    sensor_fault: List[bool] = field(default_factory=lambda: [False] * 4)
    sensor_disconnected: List[bool] = field(default_factory=lambda: [False] * 4)
    watering_issue: List[bool] = field(default_factory=lambda: [False] * 4)
    watering_locked: List[bool] = field(default_factory=lambda: [False] * 4)
    outlet_blocked: List[bool] = field(default_factory=lambda: [False] * 4)
    outlet_locked: List[bool] = field(default_factory=lambda: [False] * 4)
    water_warning: bool = False
    device_locked: bool = False
    device_id: Optional[str] = None
    version: Optional[str] = None
    firmware_update_status: str = "idle"
    firmware_update_error: str = ""
    firmware_update_started_at: str | None = None
    firmware_update_checked_at: str | None = None
    firmware_latest_version: str = ""
    firmware_update_available: bool | None = None
    device_info: Optional[DeviceInfo] = None
    channels: List[GrowcubeChannelState] = field(default_factory=lambda: [GrowcubeChannelState() for _ in range(4)])
    tank_config: GrowcubeTankConfig = field(default_factory=GrowcubeTankConfig)
    tank_state: GrowcubeTankState = field(default_factory=GrowcubeTankState)
    tank_forecast: GrowcubeTankForecast = field(default_factory=GrowcubeTankForecast)


class GrowcubeDataCoordinator(DataUpdateCoordinator[GrowcubeData]):
    def __init__(self, host: str, hass: HomeAssistant, entry: ConfigEntry):
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.client = GrowcubeClient(
            host=host,
            on_message_callback=self.handle_report,
            on_connected_callback=self.on_connected,
            on_disconnected_callback=self.on_disconnected,
        )
        self.host = host
        self.entry = entry
        self.data = GrowcubeData()
        if entry.unique_id:
            self.set_device_id(entry.unique_id)
        else:
            self.set_fallback_device_id()
        self._restore_channel_config()
        self._restore_tank_config()
        self._restore_watering_state()
        self.shutting_down = False
        self._suppress_disconnect_handler = False
        self._reconnect_task: asyncio.Task | None = None
        self._history_loading_since: list[datetime | None] = [None] * 4
        self._timed_history_refresh_requested_at: list[datetime | None] = [None] * 4
        self._history_gap_retry_at: list[datetime | None] = [None] * 4
        self._pending_apply_handles: list[asyncio.TimerHandle | None] = [None] * 4
        self._recent_manual_watering_at: list[datetime | None] = [None] * 4
        self._pending_manual_watering_amount: list[int | None] = [None] * 4
        self._last_alerts_signature: tuple[str, ...] = ()
        self._firmware_check_task: asyncio.Task | None = None
        self._unsub_history_retry = async_track_time_interval(
            hass,
            self._async_history_retry_tick,
            HISTORY_RETRY_CHECK_INTERVAL,
        )

    @property
    def device_info(self) -> DeviceInfo | None:
        return self.data.device_info

    @property
    def _connection_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_connection_problem"

    @property
    def _alerts_notification_id(self) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_alerts"

    def _watering_issue_notification_id(self, channel: int) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_watering_issue_{channel}"

    def _watering_locked_notification_id(self, channel: int) -> str:
        return f"{DOMAIN}_{self.entry.entry_id}_watering_locked_{channel}"

    def _show_connection_problem(self, message: str) -> None:
        persistent_notification.async_create(
            self.hass,
            message,
            title="GrowCube connection problem",
            notification_id=self._connection_notification_id,
        )

    def _dismiss_connection_problem(self) -> None:
        persistent_notification.async_dismiss(
            self.hass,
            self._connection_notification_id,
        )

    def _show_watering_issue_notification(self, channel: int, *, locked: bool) -> None:
        label = CHANNEL_NAME[channel]
        persistent_notification.async_create(
            self.hass,
            (
                f"Channel {label}: moisture did not rise after repeated smart watering."
                if locked
                else f"Channel {label}: moisture did not rise after smart watering."
            ),
            title="GrowCube watering alert",
            notification_id=(
                self._watering_locked_notification_id(channel)
                if locked
                else self._watering_issue_notification_id(channel)
            ),
        )

    def _dismiss_watering_issue_notifications(self, channel: int) -> None:
        persistent_notification.async_dismiss(
            self.hass,
            self._watering_issue_notification_id(channel),
        )
        persistent_notification.async_dismiss(
            self.hass,
            self._watering_locked_notification_id(channel),
        )

    def _sync_alerts_notification(self) -> None:
        problems: list[str] = []
        if self.data.water_warning:
            problems.append("- Water tank is low")
        if self.data.device_locked:
            problems.append("- GrowCube is locked")
        for channel in range(4):
            label = CHANNEL_NAME[channel]
            if self.data.outlet_blocked[channel]:
                problems.append(f"- Channel {label}: pump stall or block detected")
            if self.data.sensor_disconnected[channel]:
                problems.append(f"- Channel {label}: soil sensor is not connected")
            elif self.data.sensor_fault[channel]:
                problems.append(f"- Channel {label}: sensor reported an exception")

        signature = tuple(problems)
        if not problems:
            persistent_notification.async_dismiss(self.hass, self._alerts_notification_id)
            self._last_alerts_signature = ()
            return

        if signature == self._last_alerts_signature:
            return

        persistent_notification.async_create(
            self.hass,
            "\n".join(problems),
            title="GrowCube alerts",
            notification_id=self._alerts_notification_id,
        )
        self._last_alerts_signature = signature

    def set_device_id(self, device_id: str) -> None:
        try:
            id_str = hex(int(device_id))[2:]
        except (TypeError, ValueError):
            id_str = str(device_id)
        self.data.device_id = "growcube_{}".format(id_str)
        name = str(self.entry.options.get("device_name") or "").strip() or "GrowCube " + id_str
        self.data.device_info = DeviceInfo(
            name=name,
            identifiers={(DOMAIN, self.data.device_id)},
            manufacturer="Elecrow",
            model="Growcube",
            sw_version=self.data.version,
        )
        self.async_set_updated_data(self.data)

    def set_fallback_device_id(self) -> None:
        id_str = self.host.replace(".", "_").replace(":", "_")
        self.data.device_id = f"growcube_{id_str}"
        name = str(self.entry.options.get("device_name") or "").strip() or f"GrowCube {self.host}"
        self.data.device_info = DeviceInfo(
            name=name,
            identifiers={(DOMAIN, self.data.device_id)},
            manufacturer="Elecrow",
            model="Growcube",
            sw_version=self.data.version,
        )
        self.async_set_updated_data(self.data)

    async def async_set_device_name(self, value: str) -> None:
        """Persist a friendly GrowCube device name."""

        name = str(value or "").strip()
        if not name:
            raise HomeAssistantError("Device name cannot be empty")
        options = {**self.entry.options, "device_name": name}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        if self.data.device_id:
            self.data.device_info = DeviceInfo(
                name=name,
                identifiers={(DOMAIN, self.data.device_id)},
                manufacturer="Elecrow",
                model="Growcube",
                sw_version=self.data.version,
            )
            self.async_set_updated_data(self.data)

    async def connect(self) -> Tuple[bool, str]:
        result, error = await self.client.connect()
        if not result:
            return False, error

        self.data.connected = True
        self.async_set_updated_data(self.data)
        self._dismiss_connection_problem()
        self.shutting_down = False
        # Wait for the device to send back the DeviceVersionGrowcubeReport
        retries = 50
        while not self.data.device_id and retries > 0:
            retries -= 1
            await asyncio.sleep(0.1)

        if not self.data.device_id:
            return False, "Timed out waiting for device ID"

        _LOGGER.debug(
            "Growcube device id: %s",
            self.data.device_id
        )

        time_command = SyncTimeCommand(datetime.now())
        _LOGGER.debug(
            "%s: Sending SyncTimeCommand",
            self.data.device_id
        )
        self.client.send_command(time_command)
        self.client.send_command(RequestTankLevelCommand())
        self.client.send_command(RequestTankForecastCommand())
        self.client.send_command(RequestDelayedTimedWateringStateCommand())
        return True, ""

    async def reconnect(self) -> None:
        if self.client.connected:
            self._suppress_disconnect_handler = True
            self.client.disconnect()
            self._suppress_disconnect_handler = False

        while not self.shutting_down:
            result, error = await self.client.connect()
            if result:
                _LOGGER.debug(
                    "Reconnect to %s succeeded",
                    self.client.host
                )
                self.data.connected = True
                self.async_set_updated_data(self.data)
                self._dismiss_connection_problem()
                return

            _LOGGER.debug(
                "Reconnect failed for %s with error '%s', retrying in 10 seconds",
                self.client.host,
                error)
            self._show_connection_problem(
                f"GrowCube at {self.client.host} is unavailable: {error}. Retrying in 10 seconds."
            )
            await asyncio.sleep(10)

    def start_reconnect(self, message: str) -> None:
        self.data.connected = False
        self.async_set_updated_data(self.data)
        self._show_connection_problem(message)
        if self._reconnect_task and not self._reconnect_task.done():
            return
        self._reconnect_task = self.hass.async_create_task(self.reconnect())

    @staticmethod
    async def get_device_id(host: str) -> tuple[bool, str]:
        """This is used in the config flow to check for a valid device"""
        device_id = ""

        async def _handle_device_id_report(report: GrowcubeReport) -> None:
            if isinstance(report, DeviceVersionGrowcubeReport):
                nonlocal device_id
                device_id = report.device_id

        async def _check_device_id_assigned() -> None:
            nonlocal device_id
            while not device_id:
                await asyncio.sleep(0.1)

        client = GrowcubeClient(
            host=host,
            on_message_callback=_handle_device_id_report,
        )
        try:
            result, error = await asyncio.wait_for(client.connect(), timeout=5)
        except asyncio.TimeoutError:
            return False, "Timed out connecting to device"
        if not result:
            return False, error

        try:
            await asyncio.wait_for(_check_device_id_assigned(), timeout=5)
            client.disconnect()
        except asyncio.TimeoutError:
            client.disconnect()
            return False, "Timed out waiting for device ID"

        return True, device_id

    async def on_connected(self, host: str) -> None:
        _LOGGER.debug(
            "Connection to %s established",
            host
        )
        self.data.connected = True
        self.async_set_updated_data(self.data)
        self._dismiss_connection_problem()
        self._sync_alerts_notification()
        self.client.send_command(RequestTankLevelCommand())
        self.client.send_command(RequestTankForecastCommand())
        self.client.send_command(RequestDelayedTimedWateringStateCommand())

    async def on_disconnected(self, host: str) -> None:
        _LOGGER.debug("Connection to %s lost", host)
        self.data.connected = False
        self.data.temperature = None
        self.data.humidity = None
        self.data.moisture = [None] * 4
        self.data.pump_open = [False] * 4
        self.data.sensor_fault = [False] * 4
        self.data.sensor_disconnected = [False] * 4
        self.data.watering_issue = [False] * 4
        self.data.watering_locked = [False] * 4
        self.data.outlet_blocked = [False] * 4
        self.data.outlet_locked = [False] * 4
        self.data.water_warning = False
        self.data.device_locked = False
        for channel in range(4):
            self._dismiss_watering_issue_notifications(channel)
        self.async_set_updated_data(self.data)
        self._sync_alerts_notification()

        if not self.shutting_down and not self._suppress_disconnect_handler:
            self.start_reconnect(
                f"GrowCube at {host} is disconnected. Retrying in 10 seconds."
            )
            _LOGGER.debug(
                "Device host %s went offline, will try to reconnect",
                host
            )

    def disconnect(self) -> None:
        self.shutting_down = True
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
        for channel in range(4):
            self._cancel_pending_apply(channel)
        self._unsub_history_retry()
        self._dismiss_connection_problem()
        self.client.disconnect()

    async def handle_report(self, report: GrowcubeReport) -> None:
        new: GrowcubeData = self.data
        watering_state_changed = False
        channel_config_changed = False

        """Handle a report from the Growcube."""
        # 24 - RepDeviceVersion
        if isinstance(report, DeviceVersionGrowcubeReport):
            _LOGGER.debug(
                "Device device_id: %s, version %s",
                report.device_id,
                report.version
            )
            previous_version = self.data.version
            self.data.version = report.version
            self.set_device_id(report.device_id)
            if previous_version != report.version or self.data.firmware_update_available is None:
                self._schedule_firmware_update_check()
            return
        # 20 - RepWaterState
        elif isinstance(report, WaterStateGrowcubeReport):
            _LOGGER.debug(
                "%s: Water state %s",
                self.data.device_id,
                report.water_warning
            )
            new = self._set_scalar(new, "water_warning", report.water_warning)
        # 21 - RepSTHSate
        elif isinstance(report, MoistureHumidityStateGrowcubeReport):
            _LOGGER.debug(
                "%s: Sensor reading, channel %s, humidity %s, temperature %s, moisture %s",
                self.data.device_id,
                report.channel,
                report.humidity,
                report.temperature,
                report.moisture,
            )
            if report.temperature is not None:
                new = self._set_scalar(new, "temperature", report.temperature)
            if report.humidity is not None:
                new = self._set_scalar(new, "humidity", report.humidity)
            new = self._set_list_index(new, "sensor_fault", report.channel, False)
            new = self._set_list_index(new, "sensor_disconnected", report.channel, False)
            new = self._set_list_index(new, "moisture", report.channel.value, report.moisture)
            new = self._set_channel_state(
                new,
                report.channel.value,
                moisture=report.moisture,
                sensor_fault=False,
                sensor_disconnected=False,
            )
        # 28 - smart watering exception
        elif isinstance(report, WateringExceptionGrowcubeReport):
            _LOGGER.debug(
                "%s: Watering exception, channel %s",
                self.data.device_id,
                report.channel
            )
            new = self._set_list_index(new, "watering_issue", report.channel, True)
            new = self._set_channel_state(new, report.channel.value, watering_issue=True)
            self._show_watering_issue_notification(report.channel.value, locked=False)
        # 26 - RepPumpOpen
        elif isinstance(report, PumpOpenGrowcubeReport):
            _LOGGER.debug(
                "%s: Pump open, channel %s",
                self.data.device_id,
                report.channel
            )
            channel = report.channel.value
            pending_amount = self._pending_manual_watering_amount[channel]
            new = self._set_list_index(new, "pump_open", channel, True)
            new = self._set_channel_state(new, channel, pump_open=True)
            if pending_amount is not None:
                self._pending_manual_watering_amount[channel] = None
                timestamp = dt_util.now()
                channel_state = new.channels[channel]
                event = GrowcubeWateringEvent(
                    channel=channel,
                    timestamp=timestamp,
                    amount_ml=pending_amount,
                    source="manual",
                )
                events = list(channel_state.watering_events)
                if all(abs((existing.timestamp - event.timestamp).total_seconds()) > 30 for existing in events):
                    events.append(event)
                    events.sort(key=lambda item: item.timestamp)
                self._recent_manual_watering_at[channel] = timestamp
                new = self._set_channel_state(
                    new,
                    channel,
                    last_watering=timestamp,
                    watering_events=events,
                )
                watering_state_changed = True
        # 27 - RepPumpClose
        elif isinstance(report, PumpCloseGrowcubeReport):
            _LOGGER.debug(
                "%s: Pump closed, channel %s",
                self.data.device_id,
                report.channel
            )
            channel = report.channel.value
            self._request_firmware_tank_state()
            new = self._set_list_index(new, "pump_open", report.channel, False)
            new = self._set_channel_state(new, report.channel.value, pump_open=False)
        # Legacy sensor fault entity support
        elif isinstance(report, CheckSensorGrowcubeReport):
            _LOGGER.debug(
                "%s: Sensor abnormal, channel %s",
                self.data.device_id,
                report.channel
            )
            new = self._set_list_index(new, "sensor_fault", report.channel, True)
            new = self._set_channel_state(new, report.channel.value, sensor_fault=True)
        # 29 - Pump channel blocked
        elif isinstance(report, CheckOutletBlockedGrowcubeReport):
            _LOGGER.debug(
                "%s: Outlet blocked, channel %s",
                self.data.device_id,
                report.channel
            )
            new = self._set_list_index(new, "outlet_blocked", report.channel, True)
            new = self._set_channel_state(new, report.channel.value, outlet_blocked=True)
        # 30 - RepCheckSenSorNotConnect
        elif isinstance(report, CheckSensorNotConnectedGrowcubeReport):
            _LOGGER.debug(
                "%s: Check sensor, channel %s",
                self.data.device_id,
                report.channel
            )
            new = self._set_list_index(new, "sensor_disconnected", report.channel, True)
            new = self._set_list_index(new, "moisture", report.channel, None)
            new = self._set_channel_state(
                new,
                report.channel.value,
                moisture=None,
                sensor_disconnected=True,
            )
        # 33 - RepLockstate
        elif isinstance(report, LockStateGrowcubeReport):
            _LOGGER.debug(
                "%s: Lock state, %s",
                self.data.device_id,
                report.lock_state
            )
            # Handle case where the button on the device was pressed, this should do a reconnect
            # to read any problems still present
            if self.data.device_locked and not report.lock_state:
                self.hass.async_create_task(self.reconnect())
            new = self._set_scalar(new, "device_locked", report.lock_state)
            if report.lock_state and report.reason == 1:
                new = self._set_scalar(new, "water_warning", True)
            elif not report.lock_state:
                channels = [
                    replace(
                        channel_state,
                        outlet_locked=False,
                        watering_issue=False,
                        watering_locked=False,
                    )
                    for channel_state in new.channels
                ]
                new = replace(
                    new,
                    outlet_locked=[False] * len(new.outlet_locked),
                    watering_issue=[False] * len(new.watering_issue),
                    watering_locked=[False] * len(new.watering_locked),
                    channels=channels,
                )
                for channel in range(4):
                    self._dismiss_watering_issue_notifications(channel)
        # 34 - repeated smart watering exception lock
        elif isinstance(report, WateringExceptionLockedGrowcubeReport):
            _LOGGER.debug(
                "%s: Watering locked, channel %s",
                self.data.device_id,
                report.channel
            )
            new = self._set_list_index(new, "watering_issue", report.channel, True)
            new = self._set_list_index(new, "watering_locked", report.channel, True)
            new = self._set_channel_state(
                new,
                report.channel.value,
                watering_issue=True,
                watering_locked=True,
            )
            self._show_watering_issue_notification(report.channel.value, locked=True)
        # Legacy outlet lock entity support
        elif isinstance(report, CheckOutletLockedGrowcubeReport):
            _LOGGER.debug(
                "%s Check outlet, channel %s",
                self.data.device_id,
                report.channel
            )
            new = self._set_list_index(new, "outlet_locked", report.channel, True)
            new = self._set_channel_state(new, report.channel.value, outlet_locked=True)
        # 22 - curve/history data
        elif isinstance(report, MoistureHistoryGrowcubeReport):
            history_points = []
            zero_count = 0
            for hour, moisture in enumerate(report.values[:24]):
                if moisture <= 0:
                    zero_count += 1
                    continue
                try:
                    timestamp = datetime(
                        report.year,
                        report.month,
                        report.day,
                        hour,
                        tzinfo=dt_util.DEFAULT_TIME_ZONE,
                    )
                except ValueError:
                    continue
                history_points.append(
                    GrowcubeHistoryPoint(
                        channel=report.channel.value,
                        timestamp=timestamp,
                        moisture=max(0, min(100, moisture)),
                    )
                )
            if history_points:
                channel_state = new.channels[report.channel.value]
                existing = {
                    point.timestamp: point
                    for point in channel_state.history
                }
                for point in history_points:
                    existing[point.timestamp] = point
                history = sorted(existing.values(), key=lambda point: point.timestamp)
                new = self._set_channel_state(new, report.channel.value, history=history)
            _LOGGER.debug(
                "%s: GrowCube history day ch=%s date=%04d-%02d-%02d raw=%d accepted=%d zero=%d total_after=%d",
                self.data.device_id,
                report.channel.value,
                report.year,
                report.month,
                report.day,
                len(report.values[:24]),
                len(history_points),
                zero_count,
                len(new.channels[report.channel.value].history),
            )
        # 23 - watering record
        elif isinstance(report, WateringRecordGrowcubeReport):
            timestamp = report.timestamp.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            recent_manual = self._recent_manual_watering_at[report.channel.value]
            if recent_manual and abs((timestamp - recent_manual).total_seconds()) <= 120:
                _LOGGER.debug(
                    "%s: Ignoring history watering event that matches recent manual watering ch=%s at %s",
                    self.data.device_id,
                    report.channel.value,
                    timestamp.isoformat(),
                )
            else:
                event = GrowcubeWateringEvent(channel=report.channel.value, timestamp=timestamp)
                channel_state = new.channels[report.channel.value]
                events = list(channel_state.watering_events)
                if all(abs((existing.timestamp - event.timestamp).total_seconds()) > 30 for existing in events):
                    events.append(event)
                    events.sort(key=lambda item: item.timestamp)
                new = self._set_channel_state(
                    new,
                    report.channel.value,
                    last_watering=timestamp,
                    watering_events=events,
                )
                watering_state_changed = True
        # 56 - extended watering record with source
        elif isinstance(report, ExtendedWateringRecordGrowcubeReport):
            timestamp = report.timestamp.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            event = GrowcubeWateringEvent(
                channel=report.channel.value,
                timestamp=timestamp,
                source=report.source,
            )
            channel_state = new.channels[report.channel.value]
            events = list(channel_state.watering_events)
            for index, existing in enumerate(events):
                if abs((existing.timestamp - event.timestamp).total_seconds()) <= 30:
                    events[index] = replace(
                        existing,
                        source=report.source,
                        amount_ml=existing.amount_ml,
                    )
                    break
            else:
                events.append(event)
            events.sort(key=lambda item: item.timestamp)
            new = self._set_channel_state(
                new,
                report.channel.value,
                last_watering=timestamp,
                watering_events=events[-MAX_STORED_WATERING_EVENTS:],
            )
            watering_state_changed = True
        # 35/36 - history end marker
        elif isinstance(report, HistoryCompleteGrowcubeReport):
            channel_state = new.channels[report.channel.value]
            _LOGGER.debug(
                "%s: GrowCube history complete command=%s ch=%s success=%s points=%d events=%d",
                self.data.device_id,
                report.command,
                report.channel.value,
                report.success,
                len(channel_state.history),
                len(channel_state.watering_events),
            )
            if report.command == 35:
                loading = not channel_state.watering_events_complete and report.success
                if not loading:
                    self._history_loading_since[report.channel.value] = None
                new = self._set_channel_state(
                    new,
                    report.channel.value,
                    history_loading=loading,
                    history_complete=report.success,
                )
            elif report.command == 36:
                loading = not channel_state.history_complete and report.success
                if not loading:
                    self._history_loading_since[report.channel.value] = None
                new = self._set_channel_state(
                    new,
                    report.channel.value,
                    history_loading=loading,
                    watering_events_complete=report.success,
                )
        # 53 - persisted tank state from ESP-IDF/custom firmware
        elif isinstance(report, TankStateGrowcubeReport):
            capacity_ml = self._clamp_int(
                report.capacity_ml,
                WATER_TANK_CUSTOM_MIN_ML,
                WATER_TANK_CUSTOM_MAX_ML,
            )
            remaining_ml = self._clamp_int(report.remaining_ml, 0, capacity_ml)
            used_ml = self._clamp_int(report.used_ml, 0, capacity_ml)
            tank_config = GrowcubeTankConfig(capacity_ml=capacity_ml)
            tank_state = GrowcubeTankState(
                remaining_ml=remaining_ml,
                used_ml=used_ml,
                last_filled=self.data.tank_state.last_filled,
            )
            new = replace(new, tank_config=tank_config, tank_state=tank_state)
            self._store_tank_config_and_state(tank_state, tank_config)
        # 54 - learned 14-day tank forecast from ESP-IDF/custom firmware
        elif isinstance(report, TankForecastGrowcubeReport):
            new = replace(
                new,
                tank_forecast=GrowcubeTankForecast(
                    known=True,
                    flags=report.flags,
                    valid_days=report.valid_days,
                    confidence=report.confidence,
                    smart_daily_x10=report.smart_daily_x10,
                    manual_daily_x10=report.manual_daily_x10,
                    unknown_daily_x10=report.unknown_daily_x10,
                    smart_events=report.smart_events,
                    manual_events=report.manual_events,
                    unknown_events=report.unknown_events,
                    today_smart_ml=report.today_smart_ml,
                    today_manual_ml=report.today_manual_ml,
                    today_unknown_ml=report.today_unknown_ml,
                ),
            )
        # 55 - delayed timed watering schedule state from ESP-IDF/custom firmware
        elif isinstance(report, DelayedTimedWateringStateGrowcubeReport):
            channel_index = report.channel.value
            channel_state = new.channels[channel_index]
            config = channel_state.config
            restore_plant_id = 0
            restore_force = False
            if report.has_plant_id:
                restore_plant_id = report.plant_id
                restore_force = config.plant_id != report.plant_id
                config = replace(config, plant_id=report.plant_id)
                channel_state = replace(channel_state, config=config)
            plant_removed = report.has_plant_id and report.plant_id == 0 and report.mode == WateringMode.DISABLED
            if plant_removed:
                channels = list(new.channels)
                channels[channel_index] = GrowcubeChannelState()
                new = replace(new, channels=channels)
                channel_config_changed = True
                self._history_loading_since[channel_index] = None
                self._timed_history_refresh_requested_at[channel_index] = None
                self._history_gap_retry_at[channel_index] = None
                self._recent_manual_watering_at[channel_index] = None
                self._pending_manual_watering_amount[channel_index] = None
                self._dismiss_watering_issue_notifications(channel_index)
            elif report.mode == WateringMode.REPEATING and report.enabled:
                anchor = self._datetime_from_growcube_local_epoch(report.next_start_epoch)
                if anchor is not None and report.duration_seconds > 0 and report.interval_hours > 0:
                    new_config = replace(
                        config,
                        configured=True,
                        mode=WateringMode.REPEATING,
                        amount_ml=self._stable_watering_amount_ml(
                            report.duration_seconds,
                            config.amount_ml,
                        ),
                        duration_seconds=report.duration_seconds,
                        interval_hours=report.interval_hours,
                        first_watering_time=anchor.timetz().replace(tzinfo=None),
                        timed_watering_anchor=anchor,
                    )
                    channels = list(new.channels)
                    channels[channel_index] = replace(channel_state, config=new_config)
                    new = replace(new, channels=channels)
                    channel_config_changed = True
            elif (
                report.mode in (2, 3)
                and 0 < report.smart_min_moisture < report.smart_max_moisture <= 100
            ):
                new_config = replace(
                    config,
                    configured=True,
                    mode=WateringMode.SMART,
                    smart_min_moisture=report.smart_min_moisture,
                    smart_max_moisture=report.smart_max_moisture,
                    smart_daytime_watering=report.mode == 3,
                    timed_watering_anchor=None,
                )
                channels = list(new.channels)
                channels[channel_index] = replace(channel_state, config=new_config)
                new = replace(new, channels=channels)
                channel_config_changed = True
            elif report.has_plant_id and report.plant_id > 0:
                new_config = replace(
                    config,
                    configured=True,
                    mode=WateringMode.DISABLED,
                    timed_watering_anchor=None,
                )
                channels = list(new.channels)
                channels[channel_index] = replace(channel_state, config=new_config)
                new = replace(new, channels=channels)
                channel_config_changed = True
            elif (
                config.mode in (WateringMode.REPEATING, WateringMode.SMART)
                or config.timed_watering_anchor is not None
            ):
                new_config = replace(
                    config,
                    mode=WateringMode.DISABLED,
                    timed_watering_anchor=None,
                )
                channels = list(new.channels)
                channels[channel_index] = replace(channel_state, config=new_config)
                new = replace(new, channels=channels)
                channel_config_changed = True
            if restore_plant_id > 0:
                self.hass.async_create_task(
                    self._async_restore_channel_plant_profile(
                        channel_index,
                        restore_plant_id,
                        force=restore_force,
                    )
                )

        if new is not self.data:
            self.data = new
            if watering_state_changed:
                self._store_watering_state(new)
            if channel_config_changed:
                self._store_channel_config_for_data(new)
            self.async_set_updated_data(new)
            self._sync_alerts_notification()

    async def water_plant(self, channel: int) -> None:
        self._ensure_channel_configured(channel)
        amount_ml = self.data.channels[channel].config.manual_amount_ml
        duration = self._watering_duration_seconds(amount_ml)
        await self.client.water_plant(Channel(channel), duration)
        self._pending_manual_watering_amount[channel] = amount_ml

    async def stop_watering(self, channel: int) -> None:
        command = ClosePumpCommand(Channel(channel))
        self.client.send_command(command)

    async def async_reset_network(self) -> None:
        """Reset GrowCube network settings."""

        if not self.client.connected:
            raise HomeAssistantError("GrowCube is not connected")
        _LOGGER.warning("%s: Reset network requested", self.data.device_id)
        await self.client.reset_network()
        self.data.connected = False
        self.async_set_updated_data(self.data)
        self.start_reconnect(
            f"GrowCube at {self.host} is resetting network settings and may leave this network."
        )

    def _schedule_firmware_update_check(self) -> None:
        if self._firmware_check_task and not self._firmware_check_task.done():
            return
        self._firmware_check_task = self.hass.async_create_task(self.async_check_firmware_update(raise_error=False))

    async def async_check_firmware_update(self, raise_error: bool = True) -> None:
        """Check GrowCube's firmware server for the latest firmware version."""

        self.data.firmware_update_status = "checking"
        self.data.firmware_update_error = ""
        self.async_set_updated_data(self.data)
        try:
            info = await self.hass.async_add_executor_job(
                check_growcube_firmware_update,
                self.data.version,
            )
        except Exception as err:
            self.data.firmware_update_status = "check_error"
            self.data.firmware_update_error = str(err)
            self.data.firmware_update_checked_at = datetime.now(timezone.utc).isoformat()
            self.async_set_updated_data(self.data)
            _LOGGER.warning("%s: Firmware update check failed: %s", self.data.device_id, err)
            if raise_error:
                raise HomeAssistantError(str(err)) from err
            return

        update_available = bool(info.get("update_available"))
        latest_version = str(info.get("latest_version") or self.data.version or "")
        self.data.firmware_latest_version = latest_version
        self.data.firmware_update_available = update_available
        self.data.firmware_update_checked_at = datetime.now(timezone.utc).isoformat()
        self.data.firmware_update_error = ""
        self.data.firmware_update_status = "update_available" if update_available else "latest_installed"
        self.async_set_updated_data(self.data)

    async def async_update_firmware(self) -> None:
        """Download firmware from GrowCube and upload it to the device."""

        if not self.client.connected:
            raise HomeAssistantError("GrowCube is not connected")

        self.data.firmware_update_status = "updating"
        self.data.firmware_update_error = ""
        self.data.firmware_update_started_at = datetime.now(timezone.utc).isoformat()
        self.async_set_updated_data(self.data)
        _LOGGER.warning(
            "%s: Firmware update requested, current version=%s",
            self.data.device_id,
            self.data.version or "unknown",
        )

        firmware_path = None
        try:
            firmware_path = await self.hass.async_add_executor_job(
                download_growcube_firmware_update,
                self.data.version,
            )
            await self.client.start_firmware_update()
            await asyncio.sleep(FIRMWARE_OTA_READY_DELAY)
            await self.hass.async_add_executor_job(
                upload_firmware_image,
                self.host,
                firmware_path,
            )
        except Exception as err:
            self.data.firmware_update_status = "error"
            self.data.firmware_update_error = str(err)
            self.async_set_updated_data(self.data)
            _LOGGER.warning("%s: Firmware update failed: %s", self.data.device_id, err)
            self.start_reconnect(
                f"GrowCube at {self.host} disconnected during firmware update. Retrying in 10 seconds."
            )
            raise HomeAssistantError(str(err)) from err
        finally:
            if firmware_path is not None:
                with contextlib.suppress(OSError):
                    firmware_path.unlink()

        self.data.firmware_update_status = "uploaded"
        self.data.firmware_update_error = ""
        self.data.connected = False
        self.async_set_updated_data(self.data)
        self.client.disconnect()
        self.start_reconnect(
            f"GrowCube at {self.host} is restarting after firmware update. Retrying in 10 seconds."
        )

    async def handle_water_plant(self, channel: Channel, duration: int) -> None:
        self._ensure_channel_configured(channel.value)
        _LOGGER.debug(
            "%s: Service water_plant called, %s, %s",
            self.data.device_id,
            channel,
            duration
        )
        await self.client.water_plant(channel, duration)
        self._pending_manual_watering_amount[channel.value] = self._manual_watering_amount_clamp(
            self._watering_amount_ml(duration)
        )

    async def handle_set_manual_watering(self, channel: Channel, duration: int, interval: int) -> None:
        self._ensure_channel_configured(channel.value)

        _LOGGER.debug(
            "%s: Service set_manual_watering called, %s, %s, %s",
            self.data.device_id,
            channel,
            duration,
            interval,
        )

        self._update_channel_config(
            channel.value,
            manual_amount_ml=self._manual_watering_amount_clamp(self._watering_amount_ml(duration)),
            manual_duration_seconds=duration,
        )

        plant_id = self.data.channels[channel.value].config.plant_id
        self.client.send_command(ResetWateringModeCommand(channel, plant_id))
        self.client.send_command(TimedWateringModeCommand(channel, duration, interval, plant_id))

    async def handle_set_scheduled_watering(
        self,
        channel: Channel,
        duration: int,
        interval: int,
        first_watering_time: time | None = None,
    ) -> None:
        self._ensure_channel_configured(channel.value)

        _LOGGER.debug(
            "%s: Service set_scheduled_watering called, %s, duration=%s, interval=%s, start_time=%s",
            self.data.device_id,
            channel,
            duration,
            interval,
            first_watering_time,
        )

        configured_time = first_watering_time or self.data.channels[channel.value].config.first_watering_time
        if configured_time is None:
            configured_time = DEFAULT_FIRST_WATERING_TIME
        start_time = self._next_first_watering_datetime(configured_time)
        self._update_channel_config(
            channel.value,
            mode=WateringMode.REPEATING,
            amount_ml=self._watering_amount_ml(duration),
            duration_seconds=duration,
            interval_hours=interval,
            first_watering_time=configured_time,
            timed_watering_anchor=start_time,
        )
        self._send_timed_watering_command(channel, duration, interval, start_time)

    async def async_set_channel_name(self, channel: int, value: str) -> None:
        self._update_channel_config(channel, plant_name=value)

    async def async_set_channel_photo_url(self, channel: int, value: str) -> None:
        self._update_channel_config(channel, photo_url=value.strip())

    async def async_add_plant(
        self,
        channel: int,
        plant_name: str | None = None,
        photo_url: str | None = None,
        mode: WateringMode | None = None,
        profile: dict[str, Any] | None = None,
    ) -> None:
        changes: dict[str, Any] = {"configured": True}
        if profile:
            plant_id = self._option_int(profile.get("id"), 0)
            changes.update(
                {
                    "plant_id": max(0, plant_id),
                    "photo_url": str(profile.get("image_url") or ""),
                    "type_category": str(profile.get("category") or ""),
                    "type_description": str(profile.get("description") or ""),
                    "temp_min": self._option_int(profile.get("temp_min"), 0),
                    "temp_max": self._option_int(profile.get("temp_max"), 0),
                    "air_humidity_min": self._option_int(profile.get("air_humidity_min"), 0),
                    "air_humidity_max": self._option_int(profile.get("air_humidity_max"), 0),
                    "smart_min_moisture": self._smart_min_moisture_clamp(
                        profile.get("moisture_min"),
                        profile.get("moisture_max"),
                    ),
                    "smart_max_moisture": self._smart_max_moisture_clamp(
                        profile.get("moisture_max"),
                        profile.get("moisture_min"),
                    ),
                }
            )
            profile_name = str(profile.get("name") or profile.get("display_name") or "").strip()
            if profile_name:
                changes["plant_name"] = profile_name
        if plant_name is not None:
            changes["plant_name"] = plant_name.strip()
        if photo_url is not None:
            changes["photo_url"] = photo_url.strip()
        current_config = self.data.channels[channel].config
        if not changes.get("plant_name") and not current_config.plant_name:
            changes["plant_name"] = f"Channel {chr(ord('A') + channel)}"
        if mode is not None:
            changes["mode"] = mode
        self._update_channel_config(channel, **changes)

    async def _async_restore_channel_plant_profile(
        self,
        channel: int,
        plant_id: int,
        *,
        force: bool = False,
    ) -> None:
        plant = await async_get_plant_by_id(self.hass, plant_id)
        if not plant:
            _LOGGER.warning(
                "%s: Could not restore GrowCube plant profile id=%s channel=%s",
                self.data.device_id,
                plant_id,
                channel,
            )
            return

        current = self.data.channels[channel].config
        if current.plant_id != plant_id:
            return

        changes = self._catalog_profile_changes(current, plant, force=force)
        if not changes:
            return

        changes["configured"] = True
        self._update_channel_config(channel, **changes)

    async def async_set_first_watering_time(self, channel: int, value: time) -> None:
        self._update_channel_config(
            channel,
            first_watering_time=value,
            timed_watering_anchor=None,
        )
        self._schedule_apply_watering(channel)

    async def async_set_watering_mode(self, channel: int, mode: WateringMode) -> None:
        changes: dict[str, Any] = {"mode": mode}
        if mode == WateringMode.REPEATING:
            changes["timed_watering_anchor"] = None
        self._update_channel_config(channel, **changes)
        self._schedule_apply_watering(channel)

    async def async_set_watering_duration(self, channel: int, value: int) -> None:
        amount_ml = self._watering_amount_clamp_round(value)
        self._update_channel_config(
            channel,
            amount_ml=amount_ml,
            duration_seconds=self._watering_duration_seconds(amount_ml),
            timed_watering_anchor=None,
        )
        self._schedule_apply_watering(channel)

    async def async_set_watering_interval(self, channel: int, value: int) -> None:
        self._update_channel_config(
            channel,
            interval_hours=value,
            timed_watering_anchor=None,
        )
        self._schedule_apply_watering(channel)

    async def async_set_manual_watering_duration(self, channel: int, value: int) -> None:
        amount_ml = self._manual_watering_amount_clamp(value)
        self._update_channel_config(
            channel,
            manual_amount_ml=amount_ml,
            manual_duration_seconds=self._watering_duration_seconds(amount_ml),
        )

    async def async_set_smart_min_moisture(self, channel: int, value: int) -> None:
        config = self.data.channels[channel].config
        min_moisture = self._smart_min_moisture_clamp(value, config.smart_max_moisture)
        self._update_channel_config(channel, smart_min_moisture=min_moisture)
        self._schedule_apply_watering(channel)

    async def async_set_smart_max_moisture(self, channel: int, value: int) -> None:
        config = self.data.channels[channel].config
        max_moisture = self._smart_max_moisture_clamp(value, config.smart_min_moisture)
        self._update_channel_config(channel, smart_max_moisture=max_moisture)
        self._schedule_apply_watering(channel)

    async def async_set_smart_daytime_watering(self, channel: int, value: bool) -> None:
        self._update_channel_config(channel, smart_daytime_watering=bool(value))
        self._schedule_apply_watering(channel)

    async def async_configure_channel(
        self,
        channel: int,
        values: Mapping[str, Any],
        apply: bool = True,
    ) -> None:
        if channel < 0 or channel >= len(self.data.channels):
            raise HomeAssistantError(f"Invalid channel '{channel}' specified")

        current_config = self.data.channels[channel].config
        changes: dict[str, Any] = {}

        if "plant_id" in values:
            changes["plant_id"] = max(0, self._option_int(values.get("plant_id"), current_config.plant_id))
        if "plant_name" in values:
            changes["plant_name"] = str(values.get("plant_name") or "").strip()
        if "photo_url" in values:
            changes["photo_url"] = str(values.get("photo_url") or "").strip()
        for key in ("type_category", "type_description"):
            if key in values:
                changes[key] = str(values.get(key) or "").strip()
        for key in ("temp_min", "temp_max", "air_humidity_min", "air_humidity_max"):
            if key in values:
                changes[key] = self._option_int(values.get(key), getattr(current_config, key))

        if "mode" in values:
            changes["mode"] = self._watering_mode_from_value(values.get("mode"))

        if "first_watering_time" in values:
            changes["first_watering_time"] = self._time_from_value(values.get("first_watering_time"))
            changes["timed_watering_anchor"] = None

        if "amount_ml" in values:
            amount_ml = self._watering_amount_clamp_round(self._option_int(values.get("amount_ml"), current_config.amount_ml))
            changes["amount_ml"] = amount_ml
            changes["duration_seconds"] = self._watering_duration_seconds(amount_ml)
            changes["timed_watering_anchor"] = None
        elif "duration_seconds" in values:
            duration = self._clamp_int(values.get("duration_seconds"), 1, 60)
            changes["duration_seconds"] = duration
            changes["amount_ml"] = self._watering_amount_clamp_round(self._watering_amount_ml(duration))
            changes["timed_watering_anchor"] = None

        if "interval_hours" in values:
            changes["interval_hours"] = self._clamp_int(values.get("interval_hours"), 1, 240)
            changes["timed_watering_anchor"] = None

        min_source = values.get("smart_min_moisture", current_config.smart_min_moisture)
        max_source = values.get("smart_max_moisture", current_config.smart_max_moisture)
        if "smart_min_moisture" in values or "smart_max_moisture" in values:
            smart_min = self._smart_min_moisture_clamp(
                self._option_int(min_source, current_config.smart_min_moisture),
                self._option_int(max_source, current_config.smart_max_moisture),
            )
            smart_max = self._smart_max_moisture_clamp(
                self._option_int(max_source, current_config.smart_max_moisture),
                smart_min,
            )
            changes["smart_min_moisture"] = smart_min
            changes["smart_max_moisture"] = smart_max

        if "smart_daytime_watering" in values:
            changes["smart_daytime_watering"] = self._option_bool(
                values.get("smart_daytime_watering"),
                current_config.smart_daytime_watering,
            )

        if "configured" in values:
            changes["configured"] = self._option_bool(values.get("configured"), current_config.configured)
        elif (
            changes.get("plant_name")
            or changes.get("photo_url")
            or changes.get("mode", current_config.mode) != WateringMode.DISABLED
        ):
            changes["configured"] = True

        self._update_channel_config(channel, **changes)
        if apply:
            self._cancel_pending_apply(channel)
            await self.apply_watering_settings(channel)

    async def async_reset_plant(self, channel: int) -> None:
        await self.async_end_plant(channel)
        self._update_channel_config(channel, **asdict(GrowcubeChannelConfig()))

    async def async_request_history(self, channel: int) -> None:
        current = self.data.channels[channel]
        now = dt_util.now()
        _LOGGER.debug(
            "%s: Requesting GrowCube stored history ch=%s existing_points=%d complete=%s loading=%s",
            self.data.device_id,
            channel,
            len(current.history),
            current.history_complete,
            current.history_loading,
        )
        self.data = self._set_channel_state(
            self.data,
            channel,
            history_loading=True,
            history_complete=False,
            watering_events_complete=False,
        )
        self._history_loading_since[channel] = now
        self.async_set_updated_data(self.data)
        self.client.send_command(RequestHistoryCommand(Channel(channel)))
        self.client.send_command(RequestExtendedWateringHistoryCommand(Channel(channel)))

    async def _async_history_retry_tick(self, now: datetime) -> None:
        if self.shutting_down or not self.data.connected:
            return

        if await self._request_stale_history_retry(now):
            return
        if await self._request_due_timed_watering_history(now):
            return
        await self._request_trailing_gap_history_retry(now)

    async def _request_stale_history_retry(self, now: datetime) -> bool:
        for channel, channel_state in enumerate(self.data.channels):
            if not channel_state.history_loading:
                self._history_loading_since[channel] = None
                continue

            loading_since = self._history_loading_since[channel]
            if loading_since is None:
                self._history_loading_since[channel] = now
                continue
            if now - loading_since < HISTORY_LOADING_STALE:
                continue

            _LOGGER.warning(
                "%s: Retrying stuck history load ch=%s",
                self.data.device_id,
                channel,
            )
            await self.async_request_history(channel)
            return True
        return False

    async def _request_due_timed_watering_history(self, now: datetime) -> bool:
        for channel, channel_state in enumerate(self.data.channels):
            config = channel_state.config
            if (
                not config.configured
                or config.mode != WateringMode.REPEATING
                or config.interval_hours <= 0
                or channel_state.last_watering is None
                or channel_state.history_loading
            ):
                continue

            last_watering = channel_state.last_watering
            if last_watering.tzinfo is None:
                last_watering = last_watering.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
            expected = last_watering + timedelta(hours=config.interval_hours)
            if expected > now - TIMED_HISTORY_REFRESH_GRACE:
                continue

            last_request = self._timed_history_refresh_requested_at[channel]
            if last_request is not None and now - last_request < TIMED_HISTORY_REFRESH_RETRY:
                continue

            self._timed_history_refresh_requested_at[channel] = now
            _LOGGER.info(
                "%s: Requesting timed watering history refresh ch=%s",
                self.data.device_id,
                channel,
            )
            await self.async_request_history(channel)
            return True
        return False

    async def _request_trailing_gap_history_retry(self, now: datetime) -> bool:
        current_hour = self._history_hour_key(now)
        if current_hour is None:
            return False

        for channel, channel_state in enumerate(self.data.channels):
            if (
                not channel_state.config.configured
                or channel_state.history_loading
                or channel_state.moisture is None
                or not channel_state.history_complete
                or not channel_state.history
            ):
                continue

            last_request = self._history_gap_retry_at[channel]
            if last_request is not None and now - last_request < HISTORY_TRAILING_GAP_RETRY:
                continue

            last_hour = 0
            for point in channel_state.history:
                point_hour = self._history_hour_key(point.timestamp)
                if point_hour is not None and point_hour > last_hour:
                    last_hour = point_hour

            gap_hours = current_hour - last_hour if last_hour > 0 else HISTORY_TRAILING_GAP_HOURS + 1
            if gap_hours <= HISTORY_TRAILING_GAP_HOURS:
                continue

            self._history_gap_retry_at[channel] = now
            _LOGGER.info(
                "%s: Retrying history for trailing gap ch=%s gap=%s h",
                self.data.device_id,
                channel,
                gap_hours,
            )
            await self.async_request_history(channel)
            return True
        return False

    @staticmethod
    def _history_hour_key(value: datetime) -> int | None:
        if value.year < 2020:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        local_value = value.astimezone(dt_util.DEFAULT_TIME_ZONE)
        return local_value.toordinal() * 24 + local_value.hour

    async def async_disable_watering(self, channel: int) -> None:
        self._cancel_pending_apply(channel)
        self._update_channel_config(
            channel,
            mode=WateringMode.DISABLED,
            timed_watering_anchor=None,
        )
        self._send_disable_watering_commands(Channel(channel))
        self._clear_watering_issue_state(channel)

    async def async_end_plant(self, channel: int) -> None:
        self._update_channel_config(channel, mode=WateringMode.DISABLED)
        self._send_end_plant_commands(Channel(channel))
        self.data = self._set_channel_state(
            self.data,
            channel,
            last_watering=None,
            history_loading=False,
            history_complete=False,
            watering_events_complete=False,
            watering_issue=False,
            watering_locked=False,
            history=[],
            watering_events=[],
        )
        self.data = self._set_list_index(self.data, "watering_issue", channel, False)
        self.data = self._set_list_index(self.data, "watering_locked", channel, False)
        self._history_loading_since[channel] = None
        self._timed_history_refresh_requested_at[channel] = None
        self._history_gap_retry_at[channel] = None
        self._recent_manual_watering_at[channel] = None
        self._pending_manual_watering_amount[channel] = None
        self._dismiss_watering_issue_notifications(channel)
        self._store_watering_state(self.data)
        self.async_set_updated_data(self.data)
        self._sync_alerts_notification()

    async def async_set_repeating_watering(self, channel: int, amount_ml: int, interval: int) -> None:
        self._ensure_channel_configured(channel)
        self._cancel_pending_apply(channel)
        amount_ml = self._watering_amount_clamp_round(amount_ml)
        duration = self._watering_duration_seconds(amount_ml)
        current_config = self.data.channels[channel].config
        configured_time = current_config.first_watering_time
        if configured_time is None:
            configured_time = DEFAULT_FIRST_WATERING_TIME
        start_time = self._next_repeating_start_datetime(
            first_watering_time=configured_time,
            interval_hours=interval,
            anchor=current_config.timed_watering_anchor,
        )
        self._update_channel_config(
            channel,
            mode=WateringMode.REPEATING,
            amount_ml=amount_ml,
            duration_seconds=duration,
            interval_hours=interval,
            first_watering_time=configured_time,
            timed_watering_anchor=start_time,
        )
        self._clear_watering_issue_state(channel)
        self._send_timed_watering_command(Channel(channel), duration, interval, start_time)

    async def async_set_smart_watering(
        self,
        channel: int,
        daytime_watering: bool,
        min_moisture: int,
        max_moisture: int,
    ) -> None:
        self._ensure_channel_configured(channel)
        self._cancel_pending_apply(channel)
        min_moisture = self._clamp_int(min_moisture, SMART_MOISTURE_MIN, SMART_MOISTURE_MAX - 1)
        max_moisture = self._clamp_int(max_moisture, SMART_MOISTURE_MIN + 1, SMART_MOISTURE_MAX)
        if min_moisture >= max_moisture:
            raise HomeAssistantError(
                f"Invalid smart moisture range: max_moisture {max_moisture} must be bigger than "
                f"min_moisture {min_moisture}"
            )

        self._update_channel_config(
            channel,
            mode=WateringMode.SMART,
            smart_daytime_watering=bool(daytime_watering),
            smart_min_moisture=min_moisture,
            smart_max_moisture=max_moisture,
            timed_watering_anchor=None,
        )
        self._clear_watering_issue_state(channel)
        plant_id = self.data.channels[channel].config.plant_id
        self.client.send_command(ResetWateringModeCommand(Channel(channel), plant_id))
        self.client.send_command(
            SmartWateringModeCommand(Channel(channel), daytime_watering, min_moisture, max_moisture, plant_id)
        )

    async def apply_watering_settings(self, channel: int) -> None:
        self._ensure_channel_configured(channel)
        self._cancel_pending_apply(channel)
        config = self.data.channels[channel].config
        if config.mode == WateringMode.DISABLED:
            await self.async_disable_watering(channel)
        elif config.mode == WateringMode.REPEATING:
            await self.async_set_repeating_watering(channel, config.amount_ml, config.interval_hours)
        elif config.mode == WateringMode.SMART:
            await self.async_set_smart_watering(
                channel,
                config.smart_daytime_watering,
                config.smart_min_moisture,
                config.smart_max_moisture,
            )

    async def handle_set_smart_watering(
        self,
        channel: Channel,
        all_day: bool,
        min_moisture: int,
        max_moisture: int,
    ) -> None:
        _LOGGER.debug(
            "%s: Service set_smart_watering called, %s, all_day=%s, min=%s, max=%s",
            self.data.device_id,
            channel,
            all_day,
            min_moisture,
            max_moisture,
        )
        await self.async_set_smart_watering(channel.value, all_day, min_moisture, max_moisture)

    async def handle_delete_watering(self, channel: Channel) -> None:

        _LOGGER.debug(
            "%s: Service delete_watering called, %s,",
            self.data.device_id,
            channel
        )
        self._cancel_pending_apply(channel.value)
        self._send_disable_watering_commands(channel)
        self._update_channel_config(
            channel.value,
            mode=WateringMode.DISABLED,
            timed_watering_anchor=None,
        )
        self._clear_watering_issue_state(channel.value)

    def _send_disable_watering_commands(self, channel: Channel) -> None:
        plant_id = self.data.channels[channel.value].config.plant_id
        self.client.send_command(ClosePumpCommand(channel))
        self.client.send_command(DisableAutoWateringCommand(channel))
        self.client.send_command(ResetWateringModeCommand(channel, plant_id))

    def _send_end_plant_commands(self, channel: Channel) -> None:
        self.client.send_command(ClosePumpCommand(channel))
        self.client.send_command(PlantEndCommand(channel))
        self.client.send_command(DisableAutoWateringCommand(channel))
        self.client.send_command(ResetWateringModeCommand(channel, 0))

    def _ensure_channel_configured(self, channel: int) -> None:
        if not self.data.channels[channel].config.configured:
            raise HomeAssistantError(
                f"Channel {chr(ord('A') + channel)} has no plant configured"
            )

    def _next_first_watering_datetime(self, first_watering_time: time | None) -> datetime | None:
        if first_watering_time is None:
            return None

        now = dt_util.now()
        start_dt = datetime.combine(now.date(), first_watering_time, tzinfo=dt_util.DEFAULT_TIME_ZONE)
        if start_dt <= now:
            start_dt += timedelta(days=1)
        return start_dt

    def _next_repeating_start_datetime(
        self,
        *,
        first_watering_time: time | None,
        interval_hours: int,
        anchor: datetime | None,
    ) -> datetime | None:
        if first_watering_time is None:
            return None

        interval = timedelta(hours=max(1, int(interval_hours)))
        if anchor is None:
            return self._next_first_watering_datetime(first_watering_time)

        next_start = anchor
        if next_start.tzinfo is None:
            next_start = next_start.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        else:
            next_start = next_start.astimezone(dt_util.DEFAULT_TIME_ZONE)

        now = dt_util.now()
        while next_start <= now:
            next_start += interval
        return next_start

    @staticmethod
    def _datetime_from_growcube_local_epoch(epoch: int) -> datetime | None:
        if epoch <= 0:
            return None
        try:
            utc_components = datetime.fromtimestamp(epoch, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
        return datetime(
            utc_components.year,
            utc_components.month,
            utc_components.day,
            utc_components.hour,
            utc_components.minute,
            utc_components.second,
            tzinfo=dt_util.DEFAULT_TIME_ZONE,
        )

    def _send_timed_watering_command(
        self,
        channel: Channel,
        duration: int,
        interval: int,
        start_time: datetime | None,
    ) -> None:
        plant_id = self.data.channels[channel.value].config.plant_id
        self.client.send_command(ResetWateringModeCommand(channel, plant_id))
        self.client.send_command(TimedWateringModeCommand(channel, duration, interval, plant_id))
        if start_time is not None:
            self.client.send_command(DelayedTimedWateringCommand(channel, duration, interval, start_time, plant_id))

    def _restore_channel_config(self) -> None:
        """Restore HA-side channel settings from the config entry options."""

        stored = self.entry.options.get(CONF_CHANNEL_CONFIG, {})
        if not isinstance(stored, dict):
            return

        channels = list(self.data.channels)
        for idx, channel_state in enumerate(channels):
            raw_config = stored.get(str(idx))
            if not isinstance(raw_config, dict):
                continue

            config = self._channel_config_from_options(raw_config)
            channels[idx] = replace(channel_state, config=config)

        self.data = replace(self.data, channels=channels)

    def _restore_tank_config(self) -> None:
        """Restore HA-side tank settings from the config entry options."""

        raw_config = self.entry.options.get(CONF_TANK_CONFIG, {})
        if not isinstance(raw_config, dict):
            raw_config = {}
        capacity_ml = self._clamp_int(
            raw_config.get("capacity_ml"),
            WATER_TANK_CUSTOM_MIN_ML,
            WATER_TANK_CUSTOM_MAX_ML,
        )
        if "capacity_ml" not in raw_config:
            capacity_ml = 1500

        raw_state = self.entry.options.get(CONF_TANK_STATE, {})
        if not isinstance(raw_state, dict):
            raw_state = {}

        remaining_ml = self._clamp_int(raw_state.get("remaining_ml"), 0, capacity_ml)
        if "remaining_ml" not in raw_state:
            remaining_ml = capacity_ml
        used_ml = self._clamp_int(raw_state.get("used_ml"), 0, capacity_ml)
        last_filled = None
        raw_last_filled = raw_state.get("last_filled")
        if isinstance(raw_last_filled, str) and raw_last_filled:
            try:
                last_filled = datetime.fromisoformat(raw_last_filled)
            except ValueError:
                last_filled = None

        self.data = replace(
            self.data,
            tank_config=GrowcubeTankConfig(capacity_ml=capacity_ml),
            tank_state=GrowcubeTankState(
                remaining_ml=remaining_ml,
                used_ml=used_ml,
                last_filled=last_filled,
            ),
        )

    def _restore_watering_state(self) -> None:
        """Restore local watering notes/state similarly to the ESP-IDF diary model."""

        stored = self.entry.options.get(CONF_WATERING_STATE, {})
        if not isinstance(stored, dict):
            return

        channels = list(self.data.channels)
        for idx, channel_state in enumerate(channels):
            raw_state = stored.get(str(idx))
            if not isinstance(raw_state, dict):
                continue

            last_watering = self._option_datetime(raw_state.get("last_watering"))
            events = []
            raw_events = raw_state.get("watering_events")
            if isinstance(raw_events, list):
                for item in raw_events[-MAX_STORED_WATERING_EVENTS:]:
                    if not isinstance(item, dict):
                        continue
                    timestamp = self._option_datetime(item.get("timestamp"))
                    if timestamp is None:
                        continue
                    amount_ml = item.get("amount_ml")
                    try:
                        amount_value = int(amount_ml) if amount_ml is not None else None
                    except (TypeError, ValueError):
                        amount_value = None
                    events.append(
                        GrowcubeWateringEvent(
                            channel=idx,
                            timestamp=timestamp,
                            amount_ml=amount_value,
                            source=str(item.get("source") or "last"),
                        )
                    )

            channels[idx] = replace(
                channel_state,
                last_watering=last_watering,
                watering_events=events,
            )

        self.data = replace(self.data, channels=channels)

    def _channel_config_from_options(self, raw_config: dict[str, Any]) -> GrowcubeChannelConfig:
        first_watering_time = None
        raw_time = raw_config.get("first_watering_time")
        if isinstance(raw_time, str) and raw_time:
            try:
                first_watering_time = time.fromisoformat(raw_time)
            except ValueError:
                first_watering_time = None

        timed_watering_anchor = self._option_datetime(raw_config.get("timed_watering_anchor"))

        try:
            mode = WateringMode(raw_config.get("mode", WateringMode.DISABLED.value))
        except (TypeError, ValueError):
            mode = WateringMode.DISABLED

        return GrowcubeChannelConfig(
            configured=self._option_bool(
                raw_config.get("configured"),
                self._legacy_channel_configured(raw_config),
            ),
            plant_id=max(0, self._option_int(raw_config.get("plant_id"), 0)),
            plant_name=str(raw_config.get("plant_name", "")),
            photo_url=str(raw_config.get("photo_url", "")),
            type_category=str(raw_config.get("type_category", "")),
            type_description=str(raw_config.get("type_description", "")),
            temp_min=self._option_int(raw_config.get("temp_min"), 0),
            temp_max=self._option_int(raw_config.get("temp_max"), 0),
            air_humidity_min=self._option_int(raw_config.get("air_humidity_min"), 0),
            air_humidity_max=self._option_int(raw_config.get("air_humidity_max"), 0),
            mode=mode,
            manual_duration_seconds=self._option_int(raw_config.get("manual_duration_seconds"), 7),
            manual_amount_ml=self._manual_watering_amount_clamp(
                self._option_int(
                    raw_config.get("manual_amount_ml"),
                    self._watering_amount_ml(
                        self._option_int(raw_config.get("manual_duration_seconds"), 7)
                    ),
                )
            ),
            duration_seconds=self._option_int(raw_config.get("duration_seconds"), 7),
            amount_ml=self._watering_amount_clamp_round(
                self._option_int(
                    raw_config.get("amount_ml"),
                    self._watering_amount_ml(
                        self._option_int(raw_config.get("duration_seconds"), 7)
                    ),
                )
            ),
            interval_hours=self._option_int(raw_config.get("interval_hours"), 24),
            first_watering_time=first_watering_time,
            timed_watering_anchor=timed_watering_anchor,
            smart_min_moisture=self._smart_min_moisture_clamp(
                self._option_int(raw_config.get("smart_min_moisture"), 20),
                self._option_int(raw_config.get("smart_max_moisture"), 60),
            ),
            smart_max_moisture=self._smart_max_moisture_clamp(
                self._option_int(raw_config.get("smart_max_moisture"), 60),
                self._option_int(raw_config.get("smart_min_moisture"), 20),
            ),
            smart_daytime_watering=self._option_bool(raw_config.get("smart_daytime_watering"), True),
        )

    def _update_channel_config(self, channel: int, **changes: Any) -> None:
        channel_state = self.data.channels[channel]
        new_config = replace(channel_state.config, **changes)
        channels = list(self.data.channels)
        channels[channel] = replace(channel_state, config=new_config)
        self.data = replace(self.data, channels=channels)
        self._store_channel_config()
        self.async_set_updated_data(self.data)

    def _store_channel_config(self) -> None:
        self._store_channel_config_for_data(self.data)

    def _store_channel_config_for_data(self, data: GrowcubeData) -> None:
        options = dict(self.entry.options)
        options[CONF_CHANNEL_CONFIG] = {
            str(idx): self._channel_config_to_options(channel.config)
            for idx, channel in enumerate(data.channels)
        }
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    def _store_watering_state(self, data: GrowcubeData | None = None) -> None:
        target = data or self.data
        options = dict(self.entry.options)
        options[CONF_WATERING_STATE] = {
            str(idx): {
                "last_watering": channel.last_watering.isoformat() if channel.last_watering else None,
                "watering_events": [
                    {
                        "timestamp": event.timestamp.isoformat(),
                        "amount_ml": event.amount_ml,
                        "source": event.source,
                    }
                    for event in channel.watering_events[-MAX_STORED_WATERING_EVENTS:]
                ],
            }
            for idx, channel in enumerate(target.channels)
        }
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    async def async_set_tank_capacity(self, value: int) -> None:
        capacity_ml = self._clamp_int(
            value,
            WATER_TANK_CUSTOM_MIN_ML,
            WATER_TANK_CUSTOM_MAX_ML,
        )
        old_capacity = self.data.tank_config.capacity_ml
        remaining_ml = self.data.tank_state.remaining_ml
        if remaining_ml >= old_capacity:
            remaining_ml = capacity_ml
        else:
            remaining_ml = min(remaining_ml, capacity_ml)
        used_ml = min(self.data.tank_state.used_ml, capacity_ml)
        self.data = replace(
            self.data,
            tank_config=replace(self.data.tank_config, capacity_ml=capacity_ml),
            tank_state=replace(self.data.tank_state, remaining_ml=remaining_ml, used_ml=used_ml),
        )
        self._store_tank_config_and_state()
        self.async_set_updated_data(self.data)
        self.client.send_command(SetTankLevelCommand(capacity_ml, remaining_ml))
        self.client.send_command(RequestTankForecastCommand())

    async def async_mark_tank_full(self) -> None:
        self.data = replace(
            self.data,
            tank_state=GrowcubeTankState(
                remaining_ml=self.data.tank_config.capacity_ml,
                used_ml=0,
                last_filled=dt_util.now(),
            ),
        )
        self._store_tank_config_and_state()
        self.async_set_updated_data(self.data)
        self.client.send_command(
            SetTankLevelCommand(
                self.data.tank_config.capacity_ml,
                self.data.tank_state.remaining_ml,
            )
        )
        self.client.send_command(RequestTankForecastCommand())

    def _request_firmware_tank_state(self) -> None:
        self.client.send_command(RequestTankLevelCommand())
        self.client.send_command(RequestTankForecastCommand())

    def estimated_daily_usage_ml(self) -> float:
        forecast_usage = self._firmware_forecast_daily_usage_ml()
        if forecast_usage is not None:
            return forecast_usage

        usage = 0.0
        for channel_state in self.data.channels:
            config = channel_state.config
            if not config.configured:
                continue
            if config.mode == WateringMode.REPEATING:
                usage += config.amount_ml * 24 / max(config.interval_hours, 1)
        return usage

    def _firmware_forecast_daily_usage_ml(self) -> float | None:
        """Mirror the ESP-IDF tank_daily_usage_x10 calculation when elea54 is available."""

        forecast = self.data.tank_forecast
        if (
            not self._smart_watering_active()
            or not forecast.known
            or forecast.valid_days <= 0
            or forecast.smart_events <= 0
        ):
            return None

        usage_x10 = self._timed_daily_usage_x10()
        usage_x10 += forecast.smart_daily_x10
        usage_x10 += forecast.unknown_daily_x10
        return usage_x10 / 10

    def _timed_daily_usage_x10(self) -> int:
        usage_x10 = 0
        for channel_state in self.data.channels:
            config = channel_state.config
            if (
                not config.configured
                or config.mode != WateringMode.REPEATING
                or config.interval_hours <= 0
                or config.amount_ml <= 0
            ):
                continue
            usage_x10 += (
                config.amount_ml * 24 * 10 + config.interval_hours // 2
            ) // config.interval_hours
        return usage_x10

    def _smart_watering_active(self) -> bool:
        return any(
            channel.config.configured and channel.config.mode == WateringMode.SMART
            for channel in self.data.channels
        )

    def _store_tank_config_and_state(
        self,
        tank_state: GrowcubeTankState | None = None,
        tank_config: GrowcubeTankConfig | None = None,
    ) -> None:
        options = dict(self.entry.options)
        config = tank_config or self.data.tank_config
        options[CONF_TANK_CONFIG] = {
            "capacity_ml": config.capacity_ml,
        }
        state = tank_state or self.data.tank_state
        options[CONF_TANK_STATE] = {
            "remaining_ml": state.remaining_ml,
            "used_ml": state.used_ml,
            "last_filled": state.last_filled.isoformat() if state.last_filled else None,
        }
        self.hass.config_entries.async_update_entry(self.entry, options=options)

    def _watering_amount_ml(self, duration_seconds: int) -> int:
        seconds = max(0, int(duration_seconds))
        if seconds == 0:
            return 0
        if seconds <= 1:
            return 15
        if seconds <= 3:
            return 15 + ((seconds - 1) * (26 - 15) + (3 - 1) // 2) // (3 - 1)
        if seconds <= 4:
            return 26 + ((seconds - 3) * (37 - 26) + (4 - 3) // 2) // (4 - 3)
        if seconds <= 6:
            return 37 + ((seconds - 4) * (59 - 37) + (6 - 4) // 2) // (6 - 4)
        if seconds <= 10:
            return 59 + ((seconds - 6) * (97 - 59) + (10 - 6) // 2) // (10 - 6)
        return 97 + ((seconds - 10) * (97 - 59) + (10 - 6) // 2) // (10 - 6)

    def _watering_duration_seconds(self, amount_ml: int) -> int:
        amount_ml = max(WATER_MANUAL_AMOUNT_MIN_ML, int(amount_ml))
        seconds = (amount_ml * 10 + 99) // 84
        return self._clamp_int(seconds, 4, 60)

    def _stable_watering_amount_ml(
        self,
        duration_seconds: int,
        preferred_amount_ml: int | None = None,
    ) -> int:
        duration_seconds = self._clamp_int(int(duration_seconds), 0, 60)
        if preferred_amount_ml is not None:
            rounded_preferred = self._watering_amount_clamp_round(preferred_amount_ml)
            if self._watering_duration_seconds(rounded_preferred) == duration_seconds:
                return rounded_preferred
        return self._watering_amount_clamp_round(self._watering_amount_ml(duration_seconds))

    @staticmethod
    def _watering_amount_clamp_round(amount_ml: int) -> int:
        rounded = (
            (max(WATER_AMOUNT_MIN_ML, int(amount_ml)) + WATER_AMOUNT_STEP_ML // 2)
            // WATER_AMOUNT_STEP_ML
        ) * WATER_AMOUNT_STEP_ML
        return min(WATER_AMOUNT_MAX_ML, max(WATER_AMOUNT_MIN_ML, rounded))

    def _manual_watering_amount_clamp(self, amount_ml: int) -> int:
        rounded = self._watering_amount_clamp_round(amount_ml)
        return min(WATER_MANUAL_AMOUNT_MAX_ML, max(WATER_MANUAL_AMOUNT_MIN_ML, rounded))

    def _catalog_profile_changes(
        self,
        config: GrowcubeChannelConfig,
        plant: dict[str, Any],
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}

        def set_text(field: str, value: Any) -> None:
            text = str(value or "").strip()
            if text and (force or not str(getattr(config, field) or "").strip()):
                changes[field] = text

        def set_int(field: str, value: Any, default_empty: int = 0) -> None:
            parsed = self._option_int(value, default_empty)
            if parsed and (force or int(getattr(config, field) or 0) == default_empty):
                changes[field] = parsed

        set_text("plant_name", plant.get("display_name") or plant.get("name"))
        set_text("photo_url", plant.get("image_url"))
        set_text("type_category", plant.get("category"))
        set_text("type_description", plant.get("description"))
        set_int("temp_min", plant.get("temp_min"))
        set_int("temp_max", plant.get("temp_max"))
        set_int("air_humidity_min", plant.get("air_humidity_min"))
        set_int("air_humidity_max", plant.get("air_humidity_max"))

        return changes

    @staticmethod
    def _time_from_value(value: Any) -> time | None:
        if value in (None, ""):
            return None
        try:
            return time.fromisoformat(str(value))
        except ValueError as err:
            raise HomeAssistantError(f"Invalid first_watering_time '{value}' specified") from err

    @staticmethod
    def _watering_mode_from_value(value: Any) -> WateringMode:
        if isinstance(value, WateringMode):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in ("disabled", "off", "0"):
                return WateringMode.DISABLED
            if normalized in ("repeating", "timed", "scheduled", "1"):
                return WateringMode.REPEATING
            if normalized in ("smart", "2", "3"):
                return WateringMode.SMART
        try:
            return WateringMode(int(value))
        except (TypeError, ValueError):
            raise HomeAssistantError(f"Invalid watering mode '{value}' specified")

    @staticmethod
    def _option_datetime(value: Any) -> datetime | None:
        if not isinstance(value, str) or not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
        return parsed

    @staticmethod
    def _channel_config_to_options(config: GrowcubeChannelConfig) -> dict[str, Any]:
        return {
            "configured": config.configured,
            "plant_id": config.plant_id,
            "plant_name": config.plant_name,
            "photo_url": config.photo_url,
            "type_category": config.type_category,
            "type_description": config.type_description,
            "temp_min": config.temp_min,
            "temp_max": config.temp_max,
            "air_humidity_min": config.air_humidity_min,
            "air_humidity_max": config.air_humidity_max,
            "mode": int(config.mode),
            "manual_duration_seconds": config.manual_duration_seconds,
            "manual_amount_ml": config.manual_amount_ml,
            "duration_seconds": config.duration_seconds,
            "amount_ml": config.amount_ml,
            "interval_hours": config.interval_hours,
            "first_watering_time": config.first_watering_time.isoformat()
            if config.first_watering_time is not None
            else None,
            "timed_watering_anchor": config.timed_watering_anchor.isoformat()
            if config.timed_watering_anchor is not None
            else None,
            "smart_min_moisture": config.smart_min_moisture,
            "smart_max_moisture": config.smart_max_moisture,
            "smart_daytime_watering": config.smart_daytime_watering,
        }

    def _clear_watering_issue_state(self, channel: int) -> None:
        channel_state = self.data.channels[channel]
        if not channel_state.watering_issue and not channel_state.watering_locked and not self.data.watering_issue[channel] and not self.data.watering_locked[channel]:
            self._dismiss_watering_issue_notifications(channel)
            return
        self.data = self._set_channel_state(
            self.data,
            channel,
            watering_issue=False,
            watering_locked=False,
        )
        self.data = self._set_list_index(self.data, "watering_issue", channel, False)
        self.data = self._set_list_index(self.data, "watering_locked", channel, False)
        self._dismiss_watering_issue_notifications(channel)
        self.async_set_updated_data(self.data)
        self._sync_alerts_notification()

    def _cancel_pending_apply(self, channel: int) -> None:
        handle = self._pending_apply_handles[channel]
        if handle is not None:
            handle.cancel()
            self._pending_apply_handles[channel] = None

    def _schedule_apply_watering(self, channel: int, delay: float = 0.75) -> None:
        if channel < 0 or channel >= len(self.data.channels):
            return
        if not self.data.channels[channel].config.configured:
            return
        self._cancel_pending_apply(channel)
        loop = asyncio.get_running_loop()
        self._pending_apply_handles[channel] = loop.call_later(
            delay,
            lambda: self.hass.async_create_task(self._async_apply_watering_debounced(channel)),
        )

    async def _async_apply_watering_debounced(self, channel: int) -> None:
        self._pending_apply_handles[channel] = None
        try:
            if not self.data.channels[channel].config.configured:
                return
            await self.apply_watering_settings(channel)
        except Exception:
            _LOGGER.exception("%s: Debounced watering apply failed for channel %s", self.data.device_id, channel)

    @staticmethod
    def _legacy_channel_configured(raw_config: dict[str, Any]) -> bool:
        plant_name = str(raw_config.get("plant_name", "")).strip()
        if plant_name and plant_name not in ("Channel A", "Channel B", "Channel C", "Channel D"):
            return True
        try:
            mode = WateringMode(raw_config.get("mode", WateringMode.DISABLED.value))
        except (TypeError, ValueError):
            mode = WateringMode.DISABLED
        return mode != WateringMode.DISABLED

    @staticmethod
    def _option_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _option_bool(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("1", "true", "yes", "on"):
                return True
            if lowered in ("0", "false", "no", "off"):
                return False
        if value is None:
            return default
        return bool(value)

    @staticmethod
    def _clamp_int(value: Any, min_value: int, max_value: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = min_value
        return min(max_value, max(min_value, parsed))

    @classmethod
    def _smart_min_moisture_clamp(cls, value: Any, max_moisture: Any) -> int:
        max_value = cls._clamp_int(max_moisture, SMART_MOISTURE_MIN + 1, SMART_MOISTURE_MAX)
        value = cls._clamp_int(value, SMART_MOISTURE_MIN, SMART_MOISTURE_MAX - 1)
        return min(value, max_value - 1)

    @classmethod
    def _smart_max_moisture_clamp(cls, value: Any, min_moisture: Any) -> int:
        min_value = cls._clamp_int(min_moisture, SMART_MOISTURE_MIN, SMART_MOISTURE_MAX - 1)
        value = cls._clamp_int(value, SMART_MOISTURE_MIN + 1, SMART_MOISTURE_MAX)
        return max(value, min_value + 1)

    def _set_scalar(self, new: GrowcubeData, attr: str, value) -> GrowcubeData:
        if getattr(self.data, attr) == value:
            return new
        return replace(new, **{attr: value})

    def _set_list_index(self,
        new: GrowcubeData,
        attr: str,
        idx: int,
        value,
    ) -> GrowcubeData:
        if hasattr(idx, "value"):
            idx = idx.value
        current_list = getattr(self.data, attr)
        if current_list[idx] == value:
            return new
        copied = list(getattr(new, attr))  # copy from `new` (which may already be replaced)
        copied[idx] = value
        return replace(new, **{attr: copied})

    def _set_channel_state(self, new: GrowcubeData, channel: int, **changes: Any) -> GrowcubeData:
        channels = list(new.channels)
        channels[channel] = replace(channels[channel], **changes)
        return replace(new, channels=channels)
