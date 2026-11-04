"""Shared test helpers: tokens, a stand-in local connection, and setup entries."""

from __future__ import annotations

import base64
from collections.abc import Callable
import json
import time
from typing import Any
from unittest.mock import AsyncMock

from custom_components.delonghi_daedalus.const import (
    CONF_DEVICE_IP,
    CONF_FINGERPRINT_HANDOFF,
    CONF_SERIAL_NUMBER,
    CONF_SESSION_HANDOFF,
    CONF_THING_NAME,
    CONF_TOKEN_HANDOFF,
    CONF_USERNAME,
    DOMAIN,
)

SERIAL = "SN0001234567"
DAY = 24 * 3600
FINGERPRINT = bytes.fromhex("ab" * 32)


def make_token(expires_in: float) -> str:
    """An unsigned JWT with just an expiry, shaped like De'Longhi's."""

    def part(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    now = time.time()
    return f"{part({'alg': 'RS256'})}.{part({'iat': now, 'exp': now + expires_in})}.sig"


def entry_data(**extra: Any) -> dict[str, Any]:
    """A setup entry as the setup screens now create it: a session, never a password."""
    return {
        CONF_USERNAME: "user@example.com",
        CONF_SERIAL_NUMBER: SERIAL,
        CONF_THING_NAME: "thing",
        CONF_DEVICE_IP: "192.0.2.10",
        CONF_SESSION_HANDOFF: ["session-token", "session-secret"],
        CONF_TOKEN_HANDOFF: make_token(60 * DAY),
        CONF_FINGERPRINT_HANDOFF: FINGERPRINT.hex(),
        **extra,
    }


def stored(hass_storage: dict[str, Any], entry_id: str, **data: Any) -> None:
    """Pretend a previous run saved ``data`` in this machine's private store."""
    hass_storage[f"{DOMAIN}.{entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry_id}",
        "data": data,
    }


class StubLocal:
    """Stands in for the local connection; tests push messages in."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.callbacks: list[Callable[[dict[str, Any]], None]] = []
        self.is_connected = True
        self.identity_mismatch = False
        self.async_turn_on = AsyncMock(return_value={"Response": "OK"})
        self.async_set_settings = AsyncMock(return_value={"Response": "OK"})
        self.async_brew = AsyncMock(return_value={"Response": "OK"})

    def register_callback(self, cb):
        self.callbacks.append(cb)
        return lambda: self.callbacks.remove(cb)

    def push(self, message: dict[str, Any]) -> None:
        for cb in list(self.callbacks):
            cb(message)

    async def async_start(self) -> None:
        return None

    async def async_stop(self) -> None:
        return None
