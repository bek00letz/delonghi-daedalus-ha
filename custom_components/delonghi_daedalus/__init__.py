"""The De'Longhi Daedalus Home Assistant integration."""

from __future__ import annotations

import logging
import shutil

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
    CONF_FINGERPRINT_HANDOFF,
    CONF_PASSWORD,
    CONF_SESSION_HANDOFF,
    CONF_TOKEN_HANDOFF,
    CONF_POLL_INTERVAL,
    CONF_SERIAL_NUMBER,
    CONF_THING_NAME,
    CONF_USERNAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .coordinator import DaedalusCoordinator
from .card import async_setup_card
from .drinks import async_serve_photos, photos_root
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
    await async_serve_photos(hass)
    await async_setup_card(hass)
    store = DaedalusStore(hass, entry.entry_id)
    await store.async_load()
    _take_handoff(hass, entry, store)
    cloud_client = DaedalusCloudClient(
        aiohttp_client.async_get_clientsession(hass),
        token=store.token,
        gigya_session=store.gigya_session,
        on_token=store.set_token,
        on_session=store.set_gigya_session,
    )

    try:
        if CONF_PASSWORD in entry.data:
            # Set up before v0.7: sign in once more, keep the session, and
            # delete the stored password.
            await cloud_client.async_sign_in(username, entry.data[CONF_PASSWORD])
            hass.config_entries.async_update_entry(
                entry, data={k: v for k, v in entry.data.items() if k != CONF_PASSWORD}
            )
        elif not cloud_client.token_valid(TOKEN_RENEW_BEFORE):
            # Renew only when close to expiring; without the internet a saved
            # token that hasn't expired keeps local control working.
            await cloud_client.async_get_token(refresh=True)
    except DaedalusAuthError as err:
        # Home Assistant shows a "sign in again" prompt for this entry.
        raise ConfigEntryAuthFailed("De’Longhi no longer accepts the saved sign-in") from err
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


def _take_handoff(hass: HomeAssistant, entry: ConfigEntry, store: DaedalusStore) -> None:
    """Move secrets the setup screens handed over into the private store.

    The config entry is only a carrier: afterwards the session, token and
    machine fingerprint live in one place, the store.
    """
    keys = (CONF_SESSION_HANDOFF, CONF_TOKEN_HANDOFF, CONF_FINGERPRINT_HANDOFF)
    if not any(k in entry.data for k in keys):
        return
    if session := entry.data.get(CONF_SESSION_HANDOFF):
        store.set_gigya_session((session[0], session[1]))
    if token := entry.data.get(CONF_TOKEN_HANDOFF):
        store.set_token(token)
    if fingerprint := entry.data.get(CONF_FINGERPRINT_HANDOFF):
        store.set_machine_fingerprint(bytes.fromhex(fingerprint))
    hass.config_entries.async_update_entry(
        entry, data={k: v for k, v in entry.data.items() if k not in keys}
    )


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Sign the session out and delete the saved session, token and certificate."""
    store = DaedalusStore(hass, entry.entry_id)
    await store.async_load()
    client = DaedalusCloudClient(
        aiohttp_client.async_get_clientsession(hass), gigya_session=store.gigya_session
    )
    await client.async_sign_out()
    await store.async_remove()
    await hass.async_add_executor_job(shutil.rmtree, photos_root(hass) / entry.entry_id, True)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)

