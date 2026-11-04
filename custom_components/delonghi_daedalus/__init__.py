"""The De'Longhi Daedalus Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import aiohttp_client
from homeassistant.util import dt as dt_util

from .api.client import (
    DaedalusApiError,
    DaedalusAuthError,
    DaedalusCloudClient,
    DaedalusConnectionError,
    TOKEN_RENEW_BEFORE,
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
from .storage import DaedalusStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up De'Longhi Daedalus from a config entry."""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    store = DaedalusStore(hass, entry.entry_id)
    await store.async_load()
    cloud_client = DaedalusCloudClient(
        aiohttp_client.async_get_clientsession(hass),
        token=store.token,
        credentials=(username, password),
        on_token=store.set_token,
    )

    # Sign in only when there's no saved token or it's close to expiring.
    # Without the internet, a saved token that hasn't expired keeps local
    # control working; rejected credentials won't fix themselves.
    if not cloud_client.token_valid(TOKEN_RENEW_BEFORE):
        try:
            await cloud_client.async_authenticate(username, password)
        except DaedalusAuthError as err:
            # Home Assistant shows a "sign in again" prompt for this entry.
            raise ConfigEntryAuthFailed(
                "De’Longhi no longer accepts the saved password"
            ) from err
        except (DaedalusConnectionError, DaedalusApiError) as err:
            if not cloud_client.token_valid():
                raise ConfigEntryNotReady(f"Couldn’t reach De’Longhi cloud: {err}") from err
            _LOGGER.warning(
                "Couldn’t reach De’Longhi cloud (%s). Using the saved token, which works "
                "until %s; it renews when the cloud is back",
                err,
                dt_util.utc_from_timestamp(cloud_client.token_expires or 0).date(),
            )

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
        store=store,
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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the saved token and certificate when the integration is removed."""
    await DaedalusStore(hass, entry.entry_id).async_remove()


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)

