"""Diagnostics support for the De'Longhi Daedalus integration.

Provides a Home Assistant native diagnostics dump to triage authentication and
local LAN connectivity issues while strictly protecting user privacy. All
sensitive fields are redacted via `homeassistant.components.diagnostics.async_redact_data`.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

REDACT_KEYS: set[str] = {
    "email",
    "password",
    "username",
    "jwt",
    "session",
    "token",
    "machine_cert_sha256",
    "session_token",
    "session_secret",
    "id_token",
    "auth_token",
    "AuthToken",
    "apiKey",
    "serial_number",
    "SerialNo",
    "thing_name",
    "device_ip",
    "host",
    "lan_ip",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return a redacted diagnostics dump for a Daedalus config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    coord_state: dict[str, Any] = {}
    if coordinator is not None:
        state = getattr(coordinator, "data", None)
        coord_state = {
            "last_update_success": bool(getattr(coordinator, "last_update_success", False)),
            "last_exception": str(getattr(coordinator, "last_exception", "") or ""),
            "update_interval_s": getattr(
                getattr(coordinator, "update_interval", None), "total_seconds", lambda: None
            )(),
            "is_on": getattr(state, "is_on", None),
            "operational_state": getattr(state, "operational_state", None),
            "is_local_connected": getattr(state, "is_local_connected", None),
            "is_cloud_online": getattr(state, "is_cloud_online", None),
            "has_last_drink": getattr(state, "last_drink", None) is not None,
        }

    return {
        "entry": {
            "version": entry.version,
            "minor_version": getattr(entry, "minor_version", 1),
            "data": async_redact_data(dict(entry.data), REDACT_KEYS),
            "options": async_redact_data(dict(entry.options), REDACT_KEYS),
        },
        "coordinator": coord_state,
    }


async def async_get_device_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device: Any,
) -> dict[str, Any]:
    """Device-scoped diagnostics."""
    return await async_get_config_entry_diagnostics(hass, entry)
