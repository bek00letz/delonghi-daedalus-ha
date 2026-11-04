"""Tests for starting and running without De'Longhi's cloud, and for older setups."""

from __future__ import annotations

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
    CONF_FINGERPRINT_HANDOFF,
    CONF_PASSWORD,
    CONF_SESSION_HANDOFF,
    CONF_TOKEN_HANDOFF,
    DOMAIN,
)
from tests.helpers import DAY, FINGERPRINT, SERIAL, StubLocal, entry_data, make_token, stored

CLOUD = "custom_components.delonghi_daedalus.DaedalusCloudClient"
NO_INTERNET = DaedalusConnectionError("no internet")


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A machine set up earlier; what was handed over now lives in the store."""
    data = entry_data()
    for key in (CONF_SESSION_HANDOFF, CONF_TOKEN_HANDOFF, CONF_FINGERPRINT_HANDOFF):
        data.pop(key)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data=data)
    entry.add_to_hass(hass)
    return entry


def saved(hass_storage: dict[str, Any], entry: MockConfigEntry, token: str, **extra: Any) -> None:
    stored(
        hass_storage,
        entry.entry_id,
        token=token,
        session=["session-token", "session-secret"],
        machine_cert_sha256=FINGERPRINT.hex(),
        **extra,
    )


async def _setup_offline(hass: HomeAssistant, entry: MockConfigEntry, locals_: list[StubLocal]):
    """Set up with the cloud unreachable for everything."""
    get_jwt = AsyncMock(side_effect=NO_INTERNET)

    def make_local(**kwargs):
        locals_.append(StubLocal(**kwargs))
        return locals_[-1]

    with (
        patch(f"{CLOUD}.async_get_jwt", get_jwt),
        patch(f"{CLOUD}.async_login", AsyncMock(side_effect=NO_INTERNET)),
        patch(f"{CLOUD}.async_get_devices", side_effect=NO_INTERNET),
        patch(
            "custom_components.delonghi_daedalus.coordinator.DaedalusLocalWebSocket",
            side_effect=make_local,
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return get_jwt


def test_token_expiry_is_read_from_the_token() -> None:
    token = make_token(90 * DAY)
    assert abs(token_expiry(token) - (time.time() + 90 * DAY)) < 5
    assert token_expiry("not-a-jwt") is None


async def test_fresh_saved_token_needs_no_cloud(
    hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry
) -> None:
    token = make_token(60 * DAY)
    saved(hass_storage, entry, token)
    locals_: list[StubLocal] = []
    get_jwt = await _setup_offline(hass, entry, locals_)

    assert entry.state is ConfigEntryState.LOADED
    get_jwt.assert_not_awaited()
    # The local connection gets the saved token, no internet needed.
    assert await locals_[0].kwargs["token_provider"](False) == token


async def test_cloud_down_near_expiry_still_starts(
    hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry
) -> None:
    saved(hass_storage, entry, make_token(10 * DAY))
    get_jwt = await _setup_offline(hass, entry, [])

    get_jwt.assert_awaited()  # tried to renew
    assert entry.state is ConfigEntryState.LOADED  # but carried on with the saved token


async def test_cloud_down_with_expired_token_retries(
    hass: HomeAssistant, hass_storage: dict[str, Any], entry: MockConfigEntry
) -> None:
    saved(hass_storage, entry, make_token(-DAY))
    await _setup_offline(hass, entry, [])
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_new_token_is_saved(hass: HomeAssistant) -> None:
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
    saved(hass_storage, entry, make_token(60 * DAY))
    locals_: list[StubLocal] = []
    await _setup_offline(hass, entry, locals_)
    kwargs = locals_[0].kwargs
    assert kwargs["fingerprint"] == FINGERPRINT

    kwargs["on_identity_changed"](True)
    issue_id = f"machine_identity_changed_{SERIAL}"
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None

    kwargs["on_identity_changed"](False)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


async def test_older_setup_switches_from_password_to_session(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A setup from before v0.7 signs in once more, then the password is deleted."""
    data = entry_data(**{CONF_PASSWORD: "old-password"})
    for key in (CONF_SESSION_HANDOFF, CONF_TOKEN_HANDOFF):
        data.pop(key)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data=data)
    entry.add_to_hass(hass)

    async def sign_in(self, username, password):
        self._session_token, self._session_secret = "new-st", "new-ss"
        self._on_session(("new-st", "new-ss"))
        self._jwt_token = make_token(90 * DAY)
        self._on_token(self._jwt_token)

    with (
        patch(f"{CLOUD}.async_sign_in", sign_in),
        patch(f"{CLOUD}.async_get_devices", side_effect=NO_INTERNET),
        patch(
            "custom_components.delonghi_daedalus.coordinator.DaedalusLocalWebSocket",
            side_effect=lambda **kw: StubLocal(**kw),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert CONF_PASSWORD not in entry.data
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.store.gigya_session == ("new-st", "new-ss")
