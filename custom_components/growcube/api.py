"""HTTP API for the GrowCube integration frontend."""

from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .catalog import async_search_plants
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
            devices.append(
                {
                    "device_id": device_id,
                    "host": host,
                    "name": coordinator.device_info.get("name", device_id),
                    "connected": coordinator.data.connected,
                    "entities": self._device_entities(entity_map, device_id, host),
                    "channels": {
                        channel: self._channel_entities(entity_map, device_id, host, channel)
                        for channel in CHANNEL_ID
                    },
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
