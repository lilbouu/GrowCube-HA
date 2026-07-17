"""Local GrowCube TCP client and ELEA report parser."""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from typing import Awaitable, Callable

from .protocol import (
    build_message,
    channel_payload,
    manual_watering_payload,
    parse_messages,
    time_sync_payload,
)

_LOGGER = logging.getLogger(__name__)

GROWCUBE_PORT = 8800
WATERING_SOURCE_BY_CODE = {
    1: "smart",
    2: "timed",
    3: "manual",
}


class Channel(IntEnum):
    """GrowCube outlet channel."""

    Channel_A = 0
    Channel_B = 1
    Channel_C = 2
    Channel_D = 3


class WateringMode(IntEnum):
    """Protocol watering modes."""

    DISABLED = 0
    REPEATING = 1
    SMART_SKIP_SUNSHINE = 2
    SMART_ALL_DAY = 3


@dataclass(frozen=True, slots=True)
class GrowcubeReport:
    """Base GrowCube report."""


@dataclass(frozen=True, slots=True)
class WaterStateGrowcubeReport(GrowcubeReport):
    water_warning: bool


@dataclass(frozen=True, slots=True)
class DeviceVersionGrowcubeReport(GrowcubeReport):
    version: str
    device_id: str


@dataclass(frozen=True, slots=True)
class MoistureHumidityStateGrowcubeReport(GrowcubeReport):
    channel: Channel
    moisture: int
    humidity: int | None = None
    temperature: int | None = None


@dataclass(frozen=True, slots=True)
class PumpOpenGrowcubeReport(GrowcubeReport):
    channel: Channel


@dataclass(frozen=True, slots=True)
class PumpCloseGrowcubeReport(GrowcubeReport):
    channel: Channel


@dataclass(frozen=True, slots=True)
class CheckSensorGrowcubeReport(GrowcubeReport):
    channel: Channel


@dataclass(frozen=True, slots=True)
class WateringExceptionGrowcubeReport(GrowcubeReport):
    channel: Channel


@dataclass(frozen=True, slots=True)
class CheckOutletBlockedGrowcubeReport(GrowcubeReport):
    channel: Channel


@dataclass(frozen=True, slots=True)
class CheckSensorNotConnectedGrowcubeReport(GrowcubeReport):
    channel: Channel


@dataclass(frozen=True, slots=True)
class LockStateGrowcubeReport(GrowcubeReport):
    lock_state: bool
    reason: int = 0


@dataclass(frozen=True, slots=True)
class CheckOutletLockedGrowcubeReport(GrowcubeReport):
    channel: Channel


@dataclass(frozen=True, slots=True)
class WateringExceptionLockedGrowcubeReport(GrowcubeReport):
    channel: Channel


@dataclass(frozen=True, slots=True)
class MoistureHistoryGrowcubeReport(GrowcubeReport):
    channel: Channel
    year: int
    month: int
    day: int
    values: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class WateringRecordGrowcubeReport(GrowcubeReport):
    channel: Channel
    timestamp: datetime


@dataclass(frozen=True, slots=True)
class ExtendedWateringRecordGrowcubeReport(GrowcubeReport):
    channel: Channel
    timestamp: datetime
    source: str


@dataclass(frozen=True, slots=True)
class HistoryCompleteGrowcubeReport(GrowcubeReport):
    channel: Channel
    success: bool
    command: int


@dataclass(frozen=True, slots=True)
class TankStateGrowcubeReport(GrowcubeReport):
    remaining_ml: int
    capacity_ml: int
    used_ml: int


@dataclass(frozen=True, slots=True)
class TankForecastGrowcubeReport(GrowcubeReport):
    flags: int
    valid_days: int
    confidence: int
    smart_daily_x10: int
    manual_daily_x10: int
    unknown_daily_x10: int
    smart_events: int
    manual_events: int
    unknown_events: int
    today_smart_ml: int
    today_manual_ml: int
    today_unknown_ml: int


@dataclass(frozen=True, slots=True)
class DelayedTimedWateringStateGrowcubeReport(GrowcubeReport):
    channel: Channel
    mode: int
    enabled: bool
    duration_seconds: int
    interval_hours: int
    next_start_epoch: int
    smart_min_moisture: int = 0
    smart_max_moisture: int = 0
    plant_id: int = 0
    has_plant_id: bool = False


@dataclass(frozen=True, slots=True)
class UnknownGrowcubeReport(GrowcubeReport):
    command: int
    payload: str
    raw: str


class GrowcubeCommand:
    """ELEA command wrapper."""

    CMD_SYNC_TIME = "44"
    CMD_END_PLANT = "45"
    CMD_DISABLE_WATERING = "46"
    CMD_PUMP = "47"
    CMD_HISTORY = "48"
    CMD_WATER_MODE = "49"
    CMD_DELAYED_WATERING = "51"
    CMD_TANK_LEVEL = "52"
    CMD_TANK_FORECAST = "54"
    CMD_DELAYED_WATERING_STATE = "55"
    CMD_EXTENDED_WATERING_HISTORY = "56"

    def __init__(self, command: str | int, payload: str | None = None) -> None:
        self.command = int(command)
        self.payload = payload
        self.message = build_message(self.command, payload).decode("ascii")

    def get_description(self) -> str:
        return self.message

    def to_bytes(self) -> bytes:
        return build_message(self.command, self.payload)


class SyncTimeCommand(GrowcubeCommand):
    """Command 44 - sync GrowCube RTC."""

    def __init__(self, value: datetime) -> None:
        super().__init__(self.CMD_SYNC_TIME, time_sync_payload(value))


class OpenPumpCommand(GrowcubeCommand):
    """Command 47 - open one pump."""

    def __init__(self, channel: Channel) -> None:
        super().__init__(self.CMD_PUMP, manual_watering_payload(channel.value, True))


class ClosePumpCommand(GrowcubeCommand):
    """Command 47 - close one pump."""

    def __init__(self, channel: Channel) -> None:
        super().__init__(self.CMD_PUMP, manual_watering_payload(channel.value, False))


class RequestHistoryCommand(GrowcubeCommand):
    """Command 48 - request channel history."""

    def __init__(self, channel: Channel) -> None:
        super().__init__(self.CMD_HISTORY, channel_payload(channel.value))


class RequestExtendedWateringHistoryCommand(GrowcubeCommand):
    """Command 56 - request channel watering history with source."""

    def __init__(self, channel: Channel) -> None:
        super().__init__(self.CMD_EXTENDED_WATERING_HISTORY, channel_payload(channel.value))


class PlantEndCommand(GrowcubeCommand):
    """Command 45 - clear plant/curve data."""

    def __init__(self, channel: Channel) -> None:
        super().__init__(self.CMD_END_PLANT, channel_payload(channel.value))


class RequestTankLevelCommand(GrowcubeCommand):
    """Command 52 with empty payload - request tank state."""

    def __init__(self) -> None:
        super().__init__(self.CMD_TANK_LEVEL, "")


class SetTankLevelCommand(GrowcubeCommand):
    """Command 52 - write tank capacity and remaining amount."""

    def __init__(self, capacity_ml: int, remaining_ml: int) -> None:
        capacity_ml = max(1, int(capacity_ml))
        remaining_ml = min(capacity_ml, max(0, int(remaining_ml)))
        super().__init__(self.CMD_TANK_LEVEL, f"{capacity_ml}@{remaining_ml}")


class RequestTankForecastCommand(GrowcubeCommand):
    """Command 54 with empty payload - request firmware tank forecast."""

    def __init__(self) -> None:
        super().__init__(self.CMD_TANK_FORECAST, "")


class RequestDelayedTimedWateringStateCommand(GrowcubeCommand):
    """Command 55 - request firmware delayed timed watering state."""

    def __init__(self, channel: Channel | None = None) -> None:
        super().__init__(
            self.CMD_DELAYED_WATERING_STATE,
            "v3" if channel is None else channel_payload(channel.value),
        )


ReportCallback = Callable[[GrowcubeReport], Awaitable[None] | None]
ConnectionCallback = Callable[[str], Awaitable[None] | None]


class GrowcubeClient:
    """Async TCP client for one GrowCube device."""

    def __init__(
        self,
        host: str,
        on_message_callback: ReportCallback | None = None,
        on_connected_callback: ConnectionCallback | None = None,
        on_disconnected_callback: ConnectionCallback | None = None,
        port: int = GROWCUBE_PORT,
    ) -> None:
        self.host = host
        self.port = port
        self.on_message_callback = on_message_callback
        self.on_connected_callback = on_connected_callback
        self.on_disconnected_callback = on_disconnected_callback
        self.connected = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._manual_tasks: set[asyncio.Task] = set()
        self._disconnecting = False

    async def connect(self) -> tuple[bool, str]:
        """Open TCP connection and start receiving reports."""

        if self.connected:
            return True, ""

        self._disconnecting = False
        try:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        except OSError as err:
            return False, str(err)

        self.connected = True
        self._read_task = asyncio.create_task(self._read_loop())
        await self._maybe_call(self.on_connected_callback, self.host)
        return True, ""

    def disconnect(self) -> None:
        """Close the TCP connection."""

        self._disconnecting = True
        self.connected = False
        for task in list(self._manual_tasks):
            task.cancel()
        if self._read_task is not None:
            self._read_task.cancel()
            self._read_task = None
        if self._writer is not None:
            self._writer.close()
        self._writer = None
        self._reader = None

    def send_command(self, command: GrowcubeCommand | bytes | str) -> None:
        """Send a GrowCube command without blocking entity callbacks."""

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            _LOGGER.warning("Cannot send GrowCube command outside an event loop")
            return
        loop.create_task(self.async_send_command(command))

    async def async_send_command(self, command: GrowcubeCommand | bytes | str) -> None:
        """Send a GrowCube command."""

        if self._writer is None or self._writer.is_closing():
            _LOGGER.debug("Ignoring GrowCube command while disconnected")
            return

        if isinstance(command, GrowcubeCommand):
            data = command.to_bytes()
        elif isinstance(command, str):
            data = command.encode("ascii")
        else:
            data = command

        if data.startswith(b"elea48#") or data.startswith(b"a48#") or data.startswith(b"elea55#") or data.startswith(b"elea56#"):
            _LOGGER.debug("GrowCube history TX: %s", data.decode("ascii", errors="replace"))
        self._writer.write(data)
        await self._writer.drain()

    async def water_plant(self, channel: Channel, duration: int) -> None:
        """Open a pump for duration seconds and then close it."""

        await self.async_send_command(OpenPumpCommand(channel))
        task = asyncio.create_task(self._close_after(channel, duration))
        self._manual_tasks.add(task)
        task.add_done_callback(self._manual_tasks.discard)

    async def reset_network(self) -> None:
        """Ask GrowCube to clear Wi-Fi settings and restart."""

        await self.async_send_command(b"ele507")

    async def start_firmware_update(self) -> None:
        """Ask GrowCube to enter its OTA upload mode."""

        await self.async_send_command(b"ele504")

    async def _close_after(self, channel: Channel, duration: int) -> None:
        await asyncio.sleep(max(1, int(duration)))
        await self.async_send_command(ClosePumpCommand(channel))

    async def _read_loop(self) -> None:
        buffer = bytearray()
        try:
            while self._reader is not None:
                chunk = await self._reader.read(1024)
                if not chunk:
                    break
                buffer.extend(chunk)
                for message in parse_messages(buffer):
                    if message.command in (22, 23, 35, 36, 55, 56):
                        _LOGGER.debug(
                            "GrowCube history RX elea%s payload=%s",
                            message.command,
                            message.payload[:240],
                        )
                    report = _report_from_message(message.command, message.payload, message.raw)
                    await self._maybe_call(self.on_message_callback, report)
        except asyncio.CancelledError:
            return
        except Exception:
            _LOGGER.exception("GrowCube read loop failed")
        finally:
            was_connected = self.connected
            self.connected = False
            if was_connected and not self._disconnecting:
                await self._maybe_call(self.on_disconnected_callback, self.host)

    @staticmethod
    async def _maybe_call(callback, *args) -> None:
        if callback is None:
            return
        result = callback(*args)
        if inspect.isawaitable(result):
            await result


def _report_from_message(command: int, payload: str, raw: str) -> GrowcubeReport:
    try:
        if command == 20:
            state = int(payload)
            return WaterStateGrowcubeReport(water_warning=state == 0)
        if command == 21:
            parts = _split_ints(payload)
            if len(parts) >= 2:
                return MoistureHumidityStateGrowcubeReport(
                    channel=Channel(parts[0]),
                    moisture=parts[1],
                    humidity=parts[2] if len(parts) > 2 else None,
                    temperature=parts[3] if len(parts) > 3 else None,
                )
        if command == 22:
            fields = payload.split("@", 4)
            if len(fields) == 5:
                values = tuple(_safe_int(value) for value in fields[4].split(",") if value != "")
                return MoistureHistoryGrowcubeReport(
                    channel=Channel(int(fields[0])),
                    year=int(fields[1]),
                    month=int(fields[2]),
                    day=int(fields[3]),
                    values=values,
                )
            _LOGGER.warning("GrowCube history parse failed for elea22 payload=%s", payload[:240])
        if command == 23:
            parts = _split_ints(payload)
            if len(parts) == 6:
                return WateringRecordGrowcubeReport(
                    channel=Channel(parts[0]),
                    timestamp=datetime(parts[1], parts[2], parts[3], parts[4], parts[5]),
                )
        if command == 56:
            parts = _split_ints(payload)
            if len(parts) == 7:
                return ExtendedWateringRecordGrowcubeReport(
                    channel=Channel(parts[0]),
                    timestamp=datetime(parts[1], parts[2], parts[3], parts[4], parts[5]),
                    source=WATERING_SOURCE_BY_CODE.get(parts[6], "last"),
                )
        if command == 24:
            fields = payload.split("@")
            version = fields[0] if fields else ""
            device_id = fields[1] if len(fields) > 1 else version
            return DeviceVersionGrowcubeReport(version=version, device_id=device_id)
        if command == 26:
            return PumpOpenGrowcubeReport(channel=Channel(int(payload)))
        if command == 27:
            return PumpCloseGrowcubeReport(channel=Channel(int(payload)))
        if command == 28:
            return WateringExceptionGrowcubeReport(channel=Channel(int(payload)))
        if command == 29:
            return CheckOutletBlockedGrowcubeReport(channel=Channel(int(payload)))
        if command == 30:
            return CheckSensorNotConnectedGrowcubeReport(channel=Channel(int(payload)))
        if command == 33:
            parts = _split_ints(payload)
            if parts:
                return LockStateGrowcubeReport(
                    lock_state=parts[0] == 1,
                    reason=parts[1] if len(parts) > 1 else 0,
                )
        if command == 34:
            return WateringExceptionLockedGrowcubeReport(channel=Channel(int(payload)))
        if command in (35, 36):
            parts = _split_ints(payload)
            if len(parts) >= 2:
                return HistoryCompleteGrowcubeReport(
                    channel=Channel(parts[0]),
                    success=parts[1] == 1,
                    command=command,
                )
        if command == 53:
            parts = _split_ints(payload)
            if len(parts) == 3 and parts[1] > 0:
                remaining_ml = max(0, min(parts[0], parts[1]))
                used_ml = max(0, min(parts[2], parts[1]))
                return TankStateGrowcubeReport(
                    remaining_ml=remaining_ml,
                    capacity_ml=parts[1],
                    used_ml=used_ml,
                )
        if command == 54:
            parts = _split_ints(payload)
            if len(parts) == 13 and parts[0] == 1:
                return TankForecastGrowcubeReport(
                    flags=max(0, min(parts[1], 255)),
                    valid_days=max(0, min(parts[2], 255)),
                    confidence=max(0, min(parts[3], 255)),
                    smart_daily_x10=max(0, parts[4]),
                    manual_daily_x10=max(0, parts[5]),
                    unknown_daily_x10=max(0, parts[6]),
                    smart_events=max(0, parts[7]),
                    manual_events=max(0, parts[8]),
                    unknown_events=max(0, parts[9]),
                    today_smart_ml=max(0, min(parts[10], 65535)),
                    today_manual_ml=max(0, min(parts[11], 65535)),
                    today_unknown_ml=max(0, min(parts[12], 65535)),
                )
        if command == 55:
            parts = _split_ints(payload)
            if len(parts) >= 6 and parts[0] in (2, 3):
                mode = parts[2]
                if mode in (0, 1, 2, 3):
                    return DelayedTimedWateringStateGrowcubeReport(
                        channel=Channel(parts[1]),
                        mode=mode,
                        enabled=mode != 0,
                        duration_seconds=max(0, parts[3]) if mode == 1 else 0,
                        interval_hours=max(0, parts[4]) if mode == 1 else 0,
                        next_start_epoch=max(0, parts[5]) if mode == 1 else 0,
                        smart_min_moisture=max(0, parts[3]) if mode in (2, 3) else 0,
                        smart_max_moisture=max(0, parts[4]) if mode in (2, 3) else 0,
                        plant_id=max(0, parts[6]) if parts[0] == 3 and len(parts) > 6 else 0,
                        has_plant_id=parts[0] == 3 and len(parts) > 6,
                    )
            if len(parts) == 5:
                return DelayedTimedWateringStateGrowcubeReport(
                    channel=Channel(parts[0]),
                    mode=1 if parts[1] == 1 else 0,
                    enabled=parts[1] == 1,
                    duration_seconds=max(0, parts[2]),
                    interval_hours=max(0, parts[3]),
                    next_start_epoch=max(0, parts[4]),
                )
    except (TypeError, ValueError):
        if command in (22, 23, 35, 36, 55, 56):
            _LOGGER.warning("Could not parse GrowCube history report: %s", raw[:260])
        else:
            _LOGGER.debug("Could not parse GrowCube report: %s", raw)

    return UnknownGrowcubeReport(command=command, payload=payload, raw=raw)


def _split_ints(payload: str) -> list[int]:
    return [int(part) for part in payload.split("@") if part != ""]


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0
