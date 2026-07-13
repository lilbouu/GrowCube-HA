"""Config flow for the Growcube integration."""
from ipaddress import ip_address, ip_interface, ip_network, IPv4Network
import socket
from typing import Optional, Dict, Any

import voluptuous as vol
import asyncio
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.components import network
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from homeassistant.const import CONF_HOST

from . import GrowcubeDataCoordinator
from .const import DOMAIN

DATA_SCHEMA = {
    vol.Required(CONF_HOST): str,
}
SEARCH_SCHEMA = {
    vol.Optional("network"): str,
}
SELECT_SCHEMA = "device"

GROWCUBE_PORT = 8800
SCAN_TIMEOUT = 0.35
SCAN_CONCURRENCY = 64
MAX_SCAN_HOSTS = 254
COMMON_LAN_FALLBACK_NETWORKS = (
    "192.168.0.0/24",
    "192.168.1.0/24",
)


class GrowcubeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Growcube config flow."""
    VERSION = 2

    def __init__(self) -> None:
        self._discovered_devices: dict[str, dict[str, str]] = {}

    async def async_step_dhcp(self, discovery_info: DhcpServiceInfo) -> ConfigFlowResult:
        """Handle DHCP discovery flow."""
        host = discovery_info.ip
        # Validate device by connecting and getting device_id
        result, device_id_or_error = await GrowcubeDataCoordinator.get_device_id(host)
        if not result:
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(device_id_or_error)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        return self.async_create_entry(title=host, data={CONF_HOST: host})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["search", "manual"],
        )

    async def async_step_provision(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Explain the phone-assisted provisioning flow."""
        return self.async_show_form(
            step_id="provision",
            data_schema=vol.Schema({}),
            description_placeholders={
                "url": "/api/growcube/provision/index.html",
            },
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle manually entered GrowCube host."""
        if not user_input:
            return await self._show_manual_form()

        errors, device_id = await self._async_validate_user_input(user_input)
        if errors:
            return await self._show_manual_form(errors)

        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(updates=user_input)

        return self.async_create_entry(title=user_input[CONF_HOST],
                                       data=user_input)

    async def async_step_search(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Search the local network for GrowCube devices."""
        if user_input is None:
            user_input = {}

        errors = {}
        try:
            networks = await self._async_scan_networks(user_input.get("network") or "")
        except ValueError:
            errors["network"] = "invalid_network"
            return self.async_show_form(
                step_id="search",
                data_schema=vol.Schema(SEARCH_SCHEMA),
                errors=errors,
            )

        if not networks:
            errors["base"] = "cannot_discover"
            return self.async_show_form(
                step_id="search",
                data_schema=vol.Schema(SEARCH_SCHEMA),
                errors=errors,
            )

        devices = await self._async_discover_devices(networks)
        if not devices:
            errors["base"] = "no_devices_found"
            return self.async_show_form(
                step_id="search",
                data_schema=vol.Schema(SEARCH_SCHEMA),
                errors=errors,
            )

        if len(devices) == 1:
            device_id, host = next(iter(devices.items()))
            await self.async_set_unique_id(device_id)
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})
            return self.async_create_entry(title=host, data={CONF_HOST: host})

        self._discovered_devices = {
            device_id: {
                "host": host,
                "label": f"GrowCube {device_id} ({host})",
            }
            for device_id, host in devices.items()
        }
        return await self.async_step_select()

    async def async_step_select(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Select one of multiple discovered GrowCube devices."""
        options = {
            device_id: data["label"]
            for device_id, data in self._discovered_devices.items()
        }
        if user_input is None:
            return self.async_show_form(
                step_id="select",
                data_schema=vol.Schema({vol.Required(SELECT_SCHEMA): vol.In(options)}),
            )

        device_id = user_input[SELECT_SCHEMA]
        host = self._discovered_devices[device_id]["host"]
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        return self.async_create_entry(title=host, data={CONF_HOST: host})

    async def _async_validate_user_input(self, user_input: dict[str, Any]) -> tuple[Dict[str, str], Optional[str]]:
        """Validate the user input."""
        errors = {}
        device_id = ""
        try:
            result, value = await asyncio.wait_for(
                GrowcubeDataCoordinator.get_device_id(user_input[CONF_HOST]),
                timeout=8,
            )
        except TimeoutError:
            result = False
            value = "Timed out connecting to device"
        if not result:
            errors[CONF_HOST] = value
        else:
            device_id = value

        return errors, device_id

    async def _show_manual_form(self, errors: dict[str, str] | None = None) -> ConfigFlowResult:
        """Show the form to the user."""
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(DATA_SCHEMA),
            errors=errors if errors else {}
        )

    async def _async_scan_networks(self, user_network: str) -> list[IPv4Network]:
        """Return IPv4 networks to scan."""
        if user_network:
            return [self._normalize_network(ip_network(user_network, strict=False))]

        networks: list[IPv4Network] = []
        for adapter in await network.async_get_adapters(self.hass):
            if not adapter["enabled"]:
                continue
            for ip_info in adapter["ipv4"]:
                interface = ip_interface(
                    f"{ip_info['address']}/{ip_info['network_prefix']}"
                )
                networks.append(self._normalize_network(interface.network))

        networks.extend(self._socket_ipv4_networks())
        networks.extend(self._common_lan_fallback_networks())
        return list(dict.fromkeys(networks))

    def _common_lan_fallback_networks(self) -> list[IPv4Network]:
        """Return common LAN ranges when HA runs inside Docker."""
        networks: list[IPv4Network] = []
        for value in COMMON_LAN_FALLBACK_NETWORKS:
            networks.append(self._normalize_network(ip_network(value, strict=False)))
        return networks

    def _socket_ipv4_networks(self) -> list[IPv4Network]:
        """Return fallback IPv4 networks visible from the HA process."""

        try:
            addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        except OSError:
            return []

        networks: list[IPv4Network] = []
        for _family, _type, _proto, _canon, sockaddr in addresses:
            try:
                address = ip_address(sockaddr[0])
            except ValueError:
                continue
            if address.is_loopback or address.is_link_local:
                continue
            networks.append(self._normalize_network(ip_network(f"{address}/24", strict=False)))
        return networks

    def _normalize_network(self, scan_network: IPv4Network) -> IPv4Network:
        """Limit network scan size to keep discovery quick and polite."""
        if scan_network.version != 4:
            raise ValueError("Only IPv4 networks are supported")
        if scan_network.num_addresses <= MAX_SCAN_HOSTS + 2:
            return scan_network
        first_host = next(scan_network.hosts())
        return ip_network(f"{first_host}/24", strict=False)

    async def _async_discover_devices(self, networks: list[IPv4Network]) -> dict[str, str]:
        """Discover GrowCube devices in the given networks."""
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)
        devices: dict[str, str] = {}

        async def check_host(host: str) -> None:
            async with semaphore:
                if not await self._async_port_open(host):
                    return
                try:
                    result, device_id = await asyncio.wait_for(
                        GrowcubeDataCoordinator.get_device_id(host),
                        timeout=8,
                    )
                except (TimeoutError, asyncio.TimeoutError):
                    return
                if result:
                    devices[device_id] = host

        tasks = [
            check_host(str(host))
            for scan_network in networks
            for host in scan_network.hosts()
        ]
        await asyncio.gather(*tasks)
        return devices

    async def _async_port_open(self, host: str) -> bool:
        """Return whether the GrowCube TCP port is open."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, GROWCUBE_PORT),
                timeout=SCAN_TIMEOUT,
            )
        except (TimeoutError, OSError):
            return False
        writer.close()
        await writer.wait_closed()
        return True
