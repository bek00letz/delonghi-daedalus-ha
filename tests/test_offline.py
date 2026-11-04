"""Tests for starting and running without De'Longhi's cloud, using the saved token."""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from custom_components.delonghi_daedalus.api.client import (
    DaedalusCloudClient,
    DaedalusConnectionError,
    token_expiry,
)
from custom_components.delonghi_daedalus.const import (
    CONF_DEVICE_IP,
    CONF_PASSWORD,
    CONF_SERIAL_NUMBER,
    CONF_THING_NAME,
    CONF_USERNAME,
    DOMAIN,
)

SERIAL = "SN0001234567"
DAY = 24 * 3600
CLOUD = "custom_components.delonghi_daedalus.DaedalusCloudClient"


def make_token(expires_in: float) -> str:
    """An unsigned JWT with just an expiry, shaped like De'Longhi's."""

    def part(obj: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    now = time.time()
    return f"{part({'alg': 'RS256'})}.{part({'iat': now, 'exp': now + expires_in})}.sig"


class StubLocal:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.is_connected = True
        self.identity_mismatch = False

    def register_callback(self, cb):
        return lambda: None

    async def async_start(self) -> None:
        return None

    async def async_stop(self) -> None:
        return None


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "pw",
            CONF_SERIAL_NUMBER: SERIAL,
            CONF_THING_NAME: "thing",
            CONF_DEVICE_IP: "192.0.2.10",
        },
    )
    entry.add_to_hass(hass)
    return entry


def saved(hass_storage: dict[str, Any], entry: MockConfigEntry, **data: Any) -> None:
    """Pretend a previous run saved ``data`` for this machine."""
    hass_storage[f"{DOMAIN}.{entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{entry.entry_id}",
        "data": data,
    }


async def _setup_offline(hass: HomeAssistant, entry: MockConfigEntry, locals_: list[StubLocal]):
    """Set up with the cloud unreachable for everything."""
    authenticate = AsyncMock(side_effect=DaedalusConnectionError("no internet"))

    def make_local(**kwargs):
        locals_.append(StubLocal(**kwargs))
        return locals_[-1]

    with (
        patch(f"{CLOUD}.async_authenticate", authenticate),
        patch(f"{CLOUD}.async_get_devices", side_effect=DaedalusConnectionError("no internet")),
        patch(
            "custom_components.delonghi_daedalus.coordinator.DaedalusLocalWebSocket",
            side_effect=make_local,
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return authenticate


def test_token_expiry_is_read_from_the_token() -> None:
    token = make_token(90 * DAY)
    assert abs(token_expiry(token) - (time.time() + 90 * DAY)) < 5
    assert token_expiry("not-a-jwt") is None


async def test_fresh_saved_token_needs_no_sign_in(
    hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry
) -> None:
    saved(hass_storage, entry, token=make_token(60 * DAY))
    locals_: list[StubLocal] = []
    authenticate = await _setup_offline(hass, entry, locals_)

    assert entry.state is ConfigEntryState.LOADED
    authenticate.assert_not_awaited()
    # The local connection gets the saved token, no internet needed.
    assert await locals_[0].kwargs["token_provider"](False) == hass_storage[
        f"{DOMAIN}.{entry.entry_id}"
    ]["data"]["token"]


async def test_cloud_down_near_expiry_still_starts(
    hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry
) -> None:
    saved(hass_storage, entry, token=make_token(10 * DAY))
    authenticate = await _setup_offline(hass, entry, [])

    authenticate.assert_awaited()  # tried to renew
    assert entry.state is ConfigEntryState.LOADED  # but carried on with the saved token


async def test_cloud_down_with_expired_token_retries(
    hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry
) -> None:
    saved(hass_storage, entry, token=make_token(-DAY))
    await _setup_offline(hass, entry, [])
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_new_token_is_saved(hass: HomeAssistant, hass_storage: dict[str, Any]) -> None:
    from custom_components.delonghi_daedalus.storage import DaedalusStore

    store = DaedalusStore(hass, "abc")
    await store.async_load()
    client = DaedalusCloudClient(on_token=store.set_token)
    client._on_token("new-token")  # what async_get_jwt does on success
    await hass.async_block_till_done()
    assert store.token == "new-token"


async def test_identity_change_raises_and_clears_a_repair_notice(
    hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry
) -> None:
    saved(hass_storage, entry, token=make_token(60 * DAY), machine_cert_sha256="ab" * 32)
    locals_: list[StubLocal] = []
    await _setup_offline(hass, entry, locals_)
    kwargs = locals_[0].kwargs
    assert kwargs["fingerprint"] == bytes.fromhex("ab" * 32)

    kwargs["on_identity_changed"](True)
    issue_id = f"machine_identity_changed_{SERIAL}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    kwargs["on_identity_changed"](False)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
