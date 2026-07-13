"""HTTP API for the GrowCube integration frontend."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from urllib.parse import urlparse
import uuid

from aiohttp import ClientError
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers import entity_registry as er

from .catalog import async_get_plant_by_id, async_search_plants
from .const import CHANNEL_ID, DOMAIN
from .coordinator import GrowcubeDataCoordinator
from .provisioning import async_get_provisioning_manager


class GrowcubePlantSearchView(HomeAssistantView):
    """Expose GrowCube online plant catalog search to the Lovelace card."""

    url = "/api/growcube/plants/search"
    name = "api:growcube:plants:search"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        query = request.query.get("query", "")
        results = await async_search_plants(self.hass, query)
        return self.json({"plants": results})


class GrowcubePlantByIdView(HomeAssistantView):
    """Expose one GrowCube online plant catalog item to the Lovelace card."""

    url = "/api/growcube/plants/id"
    name = "api:growcube:plants:id"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        try:
            plant_id = int(request.query.get("id", "0"))
        except ValueError:
            plant_id = 0
        plant = await async_get_plant_by_id(self.hass, plant_id)
        return self.json({"plant": plant})


class GrowcubePlantImageView(HomeAssistantView):
    """Proxy GrowCube catalog images for the Lovelace card."""

    url = "/api/growcube/plants/image"
    name = "api:growcube:plants:image"
    _allowed_hosts = {"api.growcube.cc", "www.growcube.cc"}

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        url = str(request.query.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in self._allowed_hosts:
            return self.json({"error": "invalid_image_url"}, status_code=400)

        session = aiohttp_client.async_get_clientsession(self.hass)
        try:
            async with session.get(
                url,
                headers={
                    "Accept": "image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.8",
                    "User-Agent": "GrowCube/4.1",
                },
                timeout=20,
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip()
                if not content_type.startswith("image/"):
                    return self.json({"error": "remote_url_is_not_image"}, status_code=400)
                body = await response.read()
        except (ClientError, TimeoutError) as err:
            return self.json({"error": str(err)}, status_code=502)

        return web.Response(body=body, content_type=content_type)


class GrowcubePlantPhotoUploadView(HomeAssistantView):
    """Store uploaded plant photos under Home Assistant config/www."""

    url = "/api/growcube/plants/photo"
    name = "api:growcube:plants:photo"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        payload = await request.json() if request.can_read_body else {}
        try:
            result = await self.hass.async_add_executor_job(self._save_photo, payload)
        except ValueError as err:
            return self.json({"error": str(err)}, status_code=400)
        return self.json(result)

    def _save_photo(self, payload: dict) -> dict[str, object]:
        content_type = str(payload.get("content_type") or "").split(";", 1)[0].strip().lower()
        extension_by_type = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        suffix = extension_by_type.get(content_type)
        if suffix is None:
            raise ValueError("photo must be JPEG, PNG, or WebP")

        raw_data = str(payload.get("data") or "")
        if "," in raw_data and raw_data.lower().startswith("data:"):
            raw_data = raw_data.split(",", 1)[1]
        try:
            body = base64.b64decode(raw_data, validate=True)
        except (ValueError, binascii.Error) as err:
            raise ValueError("invalid photo data") from err

        if not body:
            raise ValueError("empty photo")
        if len(body) > 1024 * 1024:
            raise ValueError("photo must be 1 MB or smaller")

        photo_dir = Path(self.hass.config.path("www", "growcube", "plant_photos"))
        photo_dir.mkdir(parents=True, exist_ok=True)
        photo_name = f"{uuid.uuid4().hex}{suffix}"
        (photo_dir / photo_name).write_bytes(body)
        return {
            "url": f"/api/growcube/plant_photos/{photo_name}",
            "content_type": content_type,
            "bytes": len(body),
        }


class GrowcubeHistoryView(HomeAssistantView):
    """Expose coordinator-backed GrowCube history to the Lovelace card."""

    url = "/api/growcube/history"
    name = "api:growcube:history"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        channel = self._channel_index(request.query.get("channel", "a"))
        if channel is None:
            return self.json({"error": "invalid_channel"}, status_code=400)

        coordinator = self._coordinator(request.query.get("device_id", ""))
        if coordinator is None:
            return self.json({"error": "not_found"}, status_code=404)

        channel_state = coordinator.data.channels[channel]
        request_started = False
        should_request_history = (
            not channel_state.history_complete
            or not channel_state.watering_events_complete
        )
        if (
            request.query.get("request") == "1"
            and not channel_state.history_loading
            and should_request_history
        ):
            self.hass.async_create_task(coordinator.async_request_history(channel))
            request_started = True

        return self.json(
            {
                "device_id": coordinator.data.device_id,
                "channel": CHANNEL_ID[channel],
                "history_loading": channel_state.history_loading or request_started,
                "history_complete": channel_state.history_complete,
                "watering_events_complete": channel_state.watering_events_complete,
                "history_points": len(channel_state.history),
                "type_category": channel_state.config.type_category,
                "type_description": channel_state.config.type_description,
                "plant_id": channel_state.config.plant_id,
                "temp_min": channel_state.config.temp_min,
                "temp_max": channel_state.config.temp_max,
                "air_humidity_min": channel_state.config.air_humidity_min,
                "air_humidity_max": channel_state.config.air_humidity_max,
                "history": [
                    {
                        "timestamp": point.timestamp.isoformat(),
                        "moisture": point.moisture,
                    }
                    for point in channel_state.history
                ],
                "watering_events": [
                    {
                        "timestamp": event.timestamp.isoformat(),
                        "amount_ml": event.amount_ml,
                        "source": event.source,
                    }
                    for event in channel_state.watering_events
                ],
            }
        )

    def _coordinator(self, device_id: str):
        coordinators = [
            item for item in self.hass.data.get(DOMAIN, {}).values() if isinstance(item, GrowcubeDataCoordinator)
        ]
        if device_id:
            for coordinator in coordinators:
                if coordinator.data.device_id == device_id:
                    return coordinator
        return coordinators[0] if coordinators else None

    @staticmethod
    def _channel_index(value: str) -> int | None:
        text = str(value).strip().lower()
        if text in CHANNEL_ID:
            return CHANNEL_ID.index(text)
        if text.isdigit() and 0 <= int(text) < len(CHANNEL_ID):
            return int(text)
        if text.endswith(("a", "b", "c", "d")):
            return CHANNEL_ID.index(text[-1])
        return None


class GrowcubeDashboardView(HomeAssistantView):
    """Expose a full multi-device entity map to the Lovelace card."""

    url = "/api/growcube/dashboard"
    name = "api:growcube:dashboard"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        registry = er.async_get(self.hass)
        entity_map = {
            item.unique_id: item.entity_id
            for item in registry.entities.values()
            if item.platform == DOMAIN and item.unique_id and item.entity_id and not item.disabled_by
        }
        return self.json({"devices": self._devices_payload(entity_map)})

    def _devices_payload(self, entity_map: dict[str, str]) -> list[dict[str, object]]:
        devices = []
        for coordinator in self._coordinators():
            device_id = coordinator.data.device_id
            host = coordinator.host
            channels = {
                channel: {
                    **self._channel_entities(entity_map, device_id, host, channel),
                    "plant_id": coordinator.data.channels[index].config.plant_id,
                    "plant_name": coordinator.data.channels[index].config.plant_name,
                    "photo_url_value": coordinator.data.channels[index].config.photo_url,
                    "image_url": coordinator.data.channels[index].config.photo_url,
                    "photo_url_entity": self._channel_entities(entity_map, device_id, host, channel)["photo_url"],
                    "configured": coordinator.data.channels[index].config.configured,
                }
                for index, channel in enumerate(CHANNEL_ID)
            }
            entities = self._device_entities(entity_map, device_id, host)
            devices.append(
                {
                    "device_id": device_id,
                    "host": host,
                    "name": coordinator.device_info.get("name", device_id),
                    "connected": coordinator.data.connected,
                    "addon_api_url": "",
                    "entities": entities,
                    "channels": channels,
                    "states": self._entity_states(entities, channels),
                }
            )
        return sorted(devices, key=lambda item: str(item["name"]).lower())

    def _coordinators(self) -> list[GrowcubeDataCoordinator]:
        return [
            item
            for item in self.hass.data.get(DOMAIN, {}).values()
            if isinstance(item, GrowcubeDataCoordinator)
        ]

    @staticmethod
    def _lookup(entity_map: dict[str, str], unique_id: str) -> str:
        return entity_map.get(unique_id, "")

    def _entity_states(
        self,
        entities: dict[str, str],
        channels: dict[str, dict[str, object]],
    ) -> dict[str, dict[str, object]]:
        entity_ids = set(entities.values())
        for channel_entities in channels.values():
            entity_ids.update(value for value in channel_entities.values() if isinstance(value, str))

        states = {}
        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id) if entity_id else None
            if state is None:
                continue
            states[entity_id] = {
                "entity_id": entity_id,
                "state": state.state,
                "attributes": dict(state.attributes),
            }
        return states

    def _device_entities(self, entity_map: dict[str, str], device_id: str, host: str) -> dict[str, str]:
        return {
            "temperature": self._lookup(entity_map, f"{device_id}_temperature"),
            "humidity": self._lookup(entity_map, f"{device_id}_humidity"),
            "connection_problem": self._lookup(entity_map, f"{device_id}_connection_problem"),
            "water_warning": self._lookup(entity_map, f"{device_id}_water_warning"),
            "device_locked": self._lookup(entity_map, f"{device_id}_device_locked"),
            "tank_remaining": self._lookup(entity_map, f"{device_id}_tank_remaining"),
            "tank_level": self._lookup(entity_map, f"{device_id}_tank_level"),
            "tank_days_left": self._lookup(entity_map, f"{device_id}_tank_days_left"),
            "tank_capacity": self._lookup(entity_map, f"{host}_tank_capacity"),
            "mark_tank_full": self._lookup(entity_map, f"{device_id}_mark_tank_full"),
        }

    def _channel_entities(
        self,
        entity_map: dict[str, str],
        device_id: str,
        host: str,
        channel: str,
    ) -> dict[str, str]:
        return {
            "name": self._lookup(entity_map, f"{host}_plant_name_{channel}"),
            "photo_url": self._lookup(entity_map, f"{host}_plant_photo_url_{channel}"),
            "plant_configured": self._lookup(entity_map, f"{device_id}_plant_{channel}_configured"),
            "moisture": self._lookup(entity_map, f"{device_id}_moisture_{channel}"),
            "last_watering": self._lookup(entity_map, f"{device_id}_last_watering_{channel}"),
            "history_count": self._lookup(entity_map, f"{device_id}_history_count_{channel}"),
            "next_watering": self._lookup(entity_map, f"{device_id}_next_watering_{channel}"),
            "mode": self._lookup(entity_map, f"{host}_watering_mode_{channel}"),
            "first_watering_time": self._lookup(entity_map, f"{host}_first_watering_time_{channel}"),
            "duration": self._lookup(entity_map, f"{host}_duration_seconds_{channel}"),
            "interval": self._lookup(entity_map, f"{host}_interval_hours_{channel}"),
            "smart_min_moisture": self._lookup(entity_map, f"{host}_smart_min_moisture_{channel}"),
            "smart_max_moisture": self._lookup(entity_map, f"{host}_smart_max_moisture_{channel}"),
            "smart_daytime_watering": self._lookup(entity_map, f"{host}_smart_daytime_watering_{channel}"),
            "manual_duration": self._lookup(entity_map, f"{host}_manual_duration_seconds_{channel}"),
            "add_plant": self._lookup(entity_map, f"{device_id}_add_plant_{channel}"),
            "load_history": self._lookup(entity_map, f"{device_id}_load_history_{channel}"),
            "save": self._lookup(entity_map, f"{device_id}_save_schedule_{channel}"),
            "reset": self._lookup(entity_map, f"{device_id}_reset_plant_{channel}"),
            "water": self._lookup(entity_map, f"{device_id}_water_plant_{channel}"),
            "stop": self._lookup(entity_map, f"{device_id}_stop_watering_{channel}"),
            "outlet_blocked": self._lookup(entity_map, f"{device_id}_outlet_{channel}_blocked"),
            "outlet_locked": self._lookup(entity_map, f"{device_id}_outlet_{channel}_locked"),
            "sensor_fault": self._lookup(entity_map, f"{device_id}_sensor_{channel}_fault"),
            "sensor_disconnected": self._lookup(entity_map, f"{device_id}_sensor_{channel}_disconnected"),
            "watering_issue": self._lookup(entity_map, f"{device_id}_watering_issue_{channel}"),
            "watering_locked": self._lookup(entity_map, f"{device_id}_watering_locked_{channel}"),
        }


class GrowcubeChannelConfigView(HomeAssistantView):
    """Atomically update one channel config and optionally apply it to the device."""

    url = "/api/growcube/channel/config"
    name = "api:growcube:channel:config"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        channel = self._channel_index(data.get("channel", "a"))
        if channel is None:
            return self.json({"error": "invalid_channel"}, status_code=400)

        coordinator = self._coordinator(
            str(data.get("device_id") or ""),
            str(data.get("host") or ""),
        )
        if coordinator is None:
            return self.json({"error": "not_found"}, status_code=404)

        apply = self._truthy(data.get("apply", True))
        values = {
            key: value
            for key, value in data.items()
            if key not in {"device_id", "host", "channel", "apply"}
        }
        await coordinator.async_configure_channel(channel, values, apply=apply)
        config = coordinator.data.channels[channel].config
        return self.json(
            {
                "ok": True,
                "device_id": coordinator.data.device_id,
                "channel": CHANNEL_ID[channel],
                "configured": config.configured,
                "plant_id": config.plant_id,
                "plant_name": config.plant_name,
                "photo_url": config.photo_url,
                "image_url": config.photo_url,
                "type_category": config.type_category,
                "type_description": config.type_description,
                "temp_min": config.temp_min,
                "temp_max": config.temp_max,
                "air_humidity_min": config.air_humidity_min,
                "air_humidity_max": config.air_humidity_max,
                "mode": {
                    0: "Disabled",
                    1: "Repeating",
                    2: "Smart",
                }.get(int(config.mode), "Disabled"),
                "manual_duration_seconds": config.manual_amount_ml,
                "duration_seconds": config.duration_seconds,
                "amount_ml": config.amount_ml,
                "interval_hours": config.interval_hours,
                "first_watering_time": config.first_watering_time.isoformat()
                if config.first_watering_time is not None
                else None,
                "smart_min_moisture": config.smart_min_moisture,
                "smart_max_moisture": config.smart_max_moisture,
                "smart_daytime_watering": config.smart_daytime_watering,
            }
        )

    def _coordinator(self, device_id: str, host: str):
        coordinators = [
            item for item in self.hass.data.get(DOMAIN, {}).values() if isinstance(item, GrowcubeDataCoordinator)
        ]
        if device_id:
            for coordinator in coordinators:
                if coordinator.data.device_id == device_id:
                    return coordinator
        if host:
            for coordinator in coordinators:
                if coordinator.host == host:
                    return coordinator
        return coordinators[0] if len(coordinators) == 1 else None

    @staticmethod
    def _truthy(value: object) -> bool:
        if isinstance(value, str):
            return value.strip().lower() not in {"0", "false", "off", "no"}
        return bool(value)

    @staticmethod
    def _channel_index(value: object) -> int | None:
        text = str(value).strip().lower()
        if text in CHANNEL_ID:
            return CHANNEL_ID.index(text)
        if text.isdigit() and 0 <= int(text) < len(CHANNEL_ID):
            return int(text)
        if text.endswith(("a", "b", "c", "d")):
            return CHANNEL_ID.index(text[-1])
        return None


class GrowcubeProvisionSessionView(HomeAssistantView):
    """Create and update one phone provisioning session."""

    url = "/api/growcube/provision/session"
    name = "api:growcube:provision:session"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def post(self, request: web.Request) -> web.Response:
        data = await request.json() if request.can_read_body else {}
        manager = async_get_provisioning_manager(self.hass)
        token = str(data.get("token") or "").strip()
        if not token:
            session = manager.create_session()
            return self.json({
                "token": session.token,
                "provision_url": f"/api/growcube/provision/index.html?token={session.token}",
            })

        if not str(data.get("home_ssid") or "").strip():
            return self.json(
                {"error": "home_ssid_required", "message": "Home Wi-Fi SSID is required."},
                status_code=400,
            )

        session = manager.update_session(
            token,
            home_ssid=str(data.get("home_ssid") or ""),
            home_password=str(data.get("home_password") or ""),
            cube_ap_ssid=str(data.get("cube_ap_ssid") or ""),
            cube_ap_password=str(data.get("cube_ap_password") or ""),
        )
        if session is None:
            return self.json(
                {"error": "invalid_token", "message": "Provisioning session expired. Reload the page and try again."},
                status_code=404,
            )
        return self.json({
            "token": session.token,
            "helper_payload": session.helper_payload,
            "provision_url": f"/api/growcube/provision/index.html?token={session.token}",
        })


class GrowcubeProvisionStatusView(HomeAssistantView):
    """Return session status and optionally trigger LAN discovery."""

    url = "/api/growcube/provision/status"
    name = "api:growcube:provision:status"

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        token = str(request.query.get("token") or "").strip()
        if not token:
            return self.json({"error": "missing_token"}, status_code=400)

        manager = async_get_provisioning_manager(self.hass)
        discover = request.query.get("discover") == "1"
        create_entry = request.query.get("create_entry") == "1"
        session = await manager.discover_device(token) if discover else manager.get_session(token)
        if session is None:
            return self.json(
                {"error": "invalid_token", "message": "Provisioning session expired. Reload the page and start again."},
                status_code=404,
            )
        if create_entry and session.discovered_host:
            await manager.create_config_entry_for_session(token)
        return self.json({
            "token": session.token,
            "completed": session.completed,
            "discovered_host": session.discovered_host,
            "discovered_device_id": session.discovered_device_id,
            "last_error": session.last_error,
            "helper_payload": session.helper_payload,
        })
