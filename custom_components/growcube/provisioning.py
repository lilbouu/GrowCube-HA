"""Phone-assisted GrowCube provisioning helpers for Home Assistant."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import asyncio
import secrets
from ipaddress import ip_interface, ip_network, IPv4Network

from homeassistant.components import network
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .config_flow import GROWCUBE_PORT
from .coordinator import GrowcubeDataCoordinator

PROVISION_DEFAULT_AP_PASSWORD = "88888888"
PROVISION_SETUP_IP = "192.168.1.125"
PROVISION_SETUP_PORT = 8800
PROVISION_DISCOVERY_PORT = 9527
PROVISION_DISCOVERY_PAYLOAD = "Crowcube"
SESSION_TTL = timedelta(minutes=20)


@dataclass(slots=True)
class ProvisionSession:
    """One short-lived provisioning session shown on a phone."""

    token: str
    created_at: datetime
    home_ssid: str = ""
    home_password: str = ""
    cube_ap_ssid: str = ""
    cube_ap_password: str = PROVISION_DEFAULT_AP_PASSWORD
    helper_payload: dict[str, str] = field(default_factory=dict)
    discovered_host: str = ""
    discovered_device_id: str = ""
    last_error: str = ""
    completed: bool = False

    def is_expired(self, now: datetime) -> bool:
        return now - self.created_at > SESSION_TTL


class GrowcubeProvisioningManager:
    """Manage short-lived phone provisioning sessions."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._sessions: dict[str, ProvisionSession] = {}
        self._scan_lock = asyncio.Lock()

    def create_session(self) -> ProvisionSession:
        self._purge_expired()
        token = secrets.token_urlsafe(18)
        session = ProvisionSession(token=token, created_at=datetime.utcnow())
        self._sessions[token] = session
        return session

    def get_session(self, token: str) -> ProvisionSession | None:
        self._purge_expired()
        return self._sessions.get(token)

    def update_session(
        self,
        token: str,
        *,
        home_ssid: str,
        home_password: str,
        cube_ap_ssid: str,
        cube_ap_password: str,
    ) -> ProvisionSession | None:
        session = self.get_session(token)
        if session is None:
            return None
        session.home_ssid = home_ssid.strip()
        session.home_password = home_password
        session.cube_ap_ssid = cube_ap_ssid.strip()
        session.cube_ap_password = cube_ap_password or PROVISION_DEFAULT_AP_PASSWORD
        command = self.build_setup_command(session.home_ssid, session.home_password)
        command_b64 = base64.b64encode(command.encode("ascii")).decode("ascii")
        termux_helper_command = (
            "python3 -c "
            "\"import base64,socket;cmd=base64.b64decode('"
            f"{command_b64}"
            "');s=socket.create_connection(('"
            f"{PROVISION_SETUP_IP}',{PROVISION_SETUP_PORT}"
            "'),8);s.settimeout(8);s.sendall(cmd);parts=[]\n"
            "while True:\n"
            "    try: chunk=s.recv(4096)\n"
            "    except TimeoutError: break\n"
            "    if not chunk: break\n"
            "    parts.append(chunk)\n"
            "    joined=b''.join(parts)\n"
            "    if b'elea32' in joined or b'elea31#' in joined: break\n"
            "print(b''.join(parts).decode('ascii','replace'));s.close()\""
        )
        session.helper_payload = {
            "setup_ip": PROVISION_SETUP_IP,
            "setup_port": str(PROVISION_SETUP_PORT),
            "cube_ap_ssid": session.cube_ap_ssid,
            "cube_ap_password": session.cube_ap_password,
            "home_ssid": session.home_ssid,
            "home_password": session.home_password,
            "command": command,
            "command_b64": command_b64,
            "desktop_helper_command": (
                f"python3 tools/provision_growcube.py --command-b64 {command_b64}"
            ),
            "termux_helper_command": termux_helper_command,
            "success_ip_pattern": "elea32",
            "ssid_not_found_pattern": "elea31#1#1#",
            "password_error_pattern": "elea31#1#6#",
            "discovery_port": str(PROVISION_DISCOVERY_PORT),
            "discovery_payload": PROVISION_DISCOVERY_PAYLOAD,
        }
        session.last_error = ""
        session.completed = False
        session.discovered_host = ""
        session.discovered_device_id = ""
        return session

    @staticmethod
    def build_setup_command(home_ssid: str, home_password: str) -> str:
        timestamp_ms = int(datetime.utcnow().timestamp() * 1000)
        payload = f"{home_ssid}}}'{home_password}}}'{timestamp_ms}"
        return f"elea50]*{len(payload)}]*{payload}]*"

    async def discover_device(self, token: str) -> ProvisionSession | None:
        session = self.get_session(token)
        if session is None:
            return None
        async with self._scan_lock:
            try:
                host, device_id = await self._discover_one_device()
            except Exception as err:  # defensive UI path
                session.last_error = str(err)
                return session
            if host and device_id:
                session.discovered_host = host
                session.discovered_device_id = device_id
                session.completed = True
                session.last_error = ""
            else:
                session.last_error = "GrowCube not found on the current Wi-Fi yet"
        return session

    async def _discover_one_device(self) -> tuple[str, str]:
        networks = await self._local_networks()
        for scan_network in networks:
            for host in scan_network.hosts():
                host_text = str(host)
                if not await self._port_open(host_text):
                    continue
                ok, device_id = await GrowcubeDataCoordinator.get_device_id(host_text)
                if ok:
                    return host_text, device_id
        return "", ""

    async def _local_networks(self) -> list[IPv4Network]:
        networks: list[IPv4Network] = []
        for adapter in await network.async_get_adapters(self.hass):
            if not adapter["enabled"]:
                continue
            for ip_info in adapter["ipv4"]:
                interface = ip_interface(f"{ip_info['address']}/{ip_info['network_prefix']}")
                scan_network = interface.network
                if scan_network.num_addresses > 256:
                    first_host = next(scan_network.hosts())
                    scan_network = ip_network(f"{first_host}/24", strict=False)
                networks.append(scan_network)
        return list(dict.fromkeys(networks))

    async def _port_open(self, host: str) -> bool:
        try:
            _, writer = await asyncio.wait_for(asyncio.open_connection(host, GROWCUBE_PORT), timeout=0.35)
        except (TimeoutError, OSError):
            return False
        writer.close()
        await writer.wait_closed()
        return True

    async def create_config_entry_for_session(self, token: str) -> ProvisionSession | None:
        session = self.get_session(token)
        if session is None or not session.discovered_host:
            return session

        for key, item in self.hass.data.get(DOMAIN, {}).items():
            if key == "frontend_registered":
                continue
            if getattr(item, "host", None) == session.discovered_host:
                return session

        await self.hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "import"},
            data={CONF_HOST: session.discovered_host},
        )
        session.completed = True
        session.last_error = ""
        return session

    def _purge_expired(self) -> None:
        now = datetime.utcnow()
        expired = [token for token, session in self._sessions.items() if session.is_expired(now)]
        for token in expired:
            self._sessions.pop(token, None)


def async_get_provisioning_manager(hass: HomeAssistant) -> GrowcubeProvisioningManager:
    """Return the singleton provisioning manager for this integration."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    manager = domain_data.get("provisioning_manager")
    if manager is None:
        manager = GrowcubeProvisioningManager(hass)
        domain_data["provisioning_manager"] = manager
    return manager
