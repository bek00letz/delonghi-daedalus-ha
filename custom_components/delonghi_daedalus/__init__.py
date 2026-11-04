"""The De'Longhi Daedalus Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client

from .api.client import (
    DaedalusApiError,
    DaedalusAuthError,
    DaedalusCloudClient,
    DaedalusConnectionError,
)
from .api.models import DaedalusAppliance
from .const import (
    CONF_DEVICE_IP,
    CONF_MODEL,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_SERIAL_NUMBER,
    CONF_THING_NAME,
    CONF_USERNAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import DaedalusCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up De'Longhi Daedalus from a config entry."""
    session = aiohttp_client.async_get_clientsession(hass)
    cloud_client = DaedalusCloudClient(session=session)

    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]

    # Authenticate with cloud. Network trouble at boot is temporary, so Home
    # Assistant retries on its own; rejected credentials will not fix themselves.
    try:
        await cloud_client.async_authenticate(username, password)
    except DaedalusAuthError as err:
        # Home Assistant shows a "sign in again" prompt for this entry.
        raise ConfigEntryAuthFailed(
            "De’Longhi no longer accepts the saved password"
        ) from err
    except (DaedalusConnectionError, DaedalusApiError) as err:
        raise ConfigEntryNotReady(f"Couldn’t reach De’Longhi cloud: {err}") from err

    # Determine active IP (options take precedence over data)
    device_ip = entry.options.get(CONF_DEVICE_IP, entry.data.get(CONF_DEVICE_IP)) or None
    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    appliance = DaedalusAppliance(
        machine_name=entry.data[CONF_THING_NAME],
        serial_number=entry.data[CONF_SERIAL_NUMBER],
        model=entry.data.get(CONF_MODEL, "ECAM"),
        sku="",
        status="ONLINE",
        local_ip=device_ip,
    )

    coordinator = DaedalusCoordinator(
        hass=hass,
        config_entry=entry,
        cloud_client=cloud_client,
        appliance=appliance,
        poll_interval=poll_interval,
    )

    await coordinator.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: DaedalusCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_stop()

    return unload_ok


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)

