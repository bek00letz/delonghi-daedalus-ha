"""Finding the coffee machine on Home Assistant's own networks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging

from homeassistant.components import network
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .api.discovery import async_find_machine, hosts_on_networks

_LOGGER = logging.getLogger(__name__)


async def async_local_hosts(hass: HomeAssistant) -> list[str]:
    """Every address on the IPv4 networks Home Assistant is set to use."""
    adapters = await network.async_get_adapters(hass)
    return hosts_on_networks(
        (ip["address"], ip["network_prefix"])
        for adapter in adapters
        if adapter["enabled"]
        for ip in adapter["ipv4"]
    )


async def async_find_local_machine(
    hass: HomeAssistant,
    serial_number: str,
    *,
    fingerprint: bytes | None = None,
    token_provider: Callable[[], Awaitable[str]] | None = None,
) -> tuple[str, bytes] | None:
    """Look for the machine on the home network; (address, certificate fingerprint) or None."""
    hosts = await async_local_hosts(hass)
    if not hosts:
        return None
    result = await async_find_machine(
        aiohttp_client.async_get_clientsession(hass),
        hosts,
        serial_number,
        fingerprint=fingerprint,
        token_provider=token_provider,
    )
    _LOGGER.debug("Search for the coffee machine on %d addresses: %s", len(hosts), result and result[0])
    return result
