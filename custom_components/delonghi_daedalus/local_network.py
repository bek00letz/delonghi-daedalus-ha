"""Finding the coffee machine on Home Assistant's own networks."""

from __future__ import annotations

import ipaddress

from homeassistant.components import network
from homeassistant.core import HomeAssistant

from .api.discovery import async_find_candidates, async_find_machine, hosts_on_networks


async def async_local_hosts(hass: HomeAssistant) -> list[str]:
    """Every address on the IPv4 networks Home Assistant is set to use."""
    adapters = await network.async_get_adapters(hass)
    return hosts_on_networks(
        (ip["address"], ip["network_prefix"])
        for adapter in adapters
        if adapter["enabled"]
        for ip in adapter["ipv4"]
    )


def is_local_address(value: str) -> bool:
    """True for a plain IPv4 address on a private home network (no port, no name)."""
    try:
        address = ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return address.is_private and not (address.is_loopback or address.is_link_local)


async def async_find_local_candidates(
    hass: HomeAssistant, first: list[str] | None = None
) -> list[tuple[str, bytes]]:
    """Devices on the home network that look like a coffee machine (not yet trusted).

    ``first`` (the address the machine reports to the cloud) is looked at
    first and included even if it's on a network Home Assistant doesn't scan.
    """
    first = [h for h in (first or []) if is_local_address(h)]
    hosts = list(dict.fromkeys(first + await async_local_hosts(hass)))
    return await async_find_candidates(hosts) if hosts else []


async def async_find_local_machine(hass: HomeAssistant, fingerprint: bytes) -> str | None:
    """Where the already-trusted machine is now, matched by certificate only."""
    hosts = await async_local_hosts(hass)
    return await async_find_machine(hosts, fingerprint) if hosts else None
