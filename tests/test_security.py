"""Regression tests for the 2026-11 security review: each attack or failure must now fail safe.

(The findings themselves are kept privately; these tests keep the fixes in place.)
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.delonghi_daedalus.api.client import DaedalusAuthError
from custom_components.delonghi_daedalus.api.cloud import DaedalusCloudConnection
from custom_components.delonghi_daedalus.api.models import DaedalusAppliance
from custom_components.delonghi_daedalus.const import CONF_TOKEN_HANDOFF, DOMAIN
from tests.helpers import DAY, SERIAL, StubLocal, entry_data, make_token

CLOUD = "custom_components.delonghi_daedalus.DaedalusCloudClient"


def _report(status: str, **alarms: bool) -> dict[str, Any]:
    body = {"Status": status, "CurrMode": 7, "alarms": {"TankLevel": False, **alarms}}
    return {"Message": "StatusService", "Response": json.dumps(body)}


@pytest.fixture
async def setup(hass: HomeAssistant):
    """The integration running against a stand-in machine; yields (entry, machine)."""
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=SERIAL, data=entry_data(**{CONF_TOKEN_HANDOFF: make_token(40 * DAY)})
    )
    entry.add_to_hass(hass)
    machines: list[StubLocal] = []
    with (
        patch(
            f"{CLOUD}.async_get_devices",
            AsyncMock(return_value=[DaedalusAppliance("thing", SERIAL, "ECAM47080", "", "ONLINE")]),
        ),
        patch(
            "custom_components.delonghi_daedalus.coordinator.DaedalusLocalWebSocket",
            side_effect=lambda **kw: machines.append(StubLocal(**kw)) or machines[-1],
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield entry, machines[0]


def _entity(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{SERIAL}_{key}")
    assert entity_id
    return entity_id


async def test_a_refused_sign_in_while_running_asks_once_and_stops(hass: HomeAssistant, setup) -> None:
    """Changing the password elsewhere: one attempt and a "sign in again" prompt, no storm."""
    entry, _ = setup
    coordinator = hass.data[DOMAIN][entry.entry_id]
    coordinator.cloud_client._jwt_token = make_token(20 * DAY)  # now in the renewal window
    get_jwt = AsyncMock(side_effect=DaedalusAuthError("403005"))
    with patch(f"{CLOUD}.async_get_jwt", get_jwt):
        for _ in range(5):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
    assert get_jwt.await_count == 1
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == ["reauth"]


@pytest.mark.parametrize(
    "report",
    [
        _report("Busy"),
        _report("Idle", TankLevel=True),
        _report("Idle", Waste=True),
        _report("Idle", NoCoffee=True),
        _report("Standby"),
    ],
)
async def test_no_drink_unless_ready_and_clear(hass: HomeAssistant, setup, report) -> None:
    _, machine = setup
    button = _entity(hass, "button", "brew_hot_water")
    machine.push(_report("Idle"))  # ready and clear: drinks allowed
    await hass.async_block_till_done()
    assert hass.states.get(button).state != "unavailable"

    machine.push(report)
    await hass.async_block_till_done()
    assert hass.states.get(button).state == "unavailable"


async def test_a_press_is_rechecked_at_the_moment_it_happens(hass: HomeAssistant, setup) -> None:
    entry, machine = setup
    machine.push(_report("Idle"))
    await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    button = next(
        e for e in hass.data["entity_components"]["button"].entities if e.entity_id == _entity(hass, "button", "brew_hot_water")
    )
    machine.push(_report("Busy"))  # became busy without the button being redrawn yet
    with pytest.raises(HomeAssistantError) as err:
        await button.async_press()
    assert err.value.translation_key == "cannot_brew_now"
    machine.async_brew.assert_not_awaited()
    assert coordinator.can_brew is False


async def test_settings_controls_wait_for_the_current_settings(hass: HomeAssistant, setup) -> None:
    """Before settings are known a switch can't send half a settings group."""
    assert hass.states.get(_entity(hass, "switch", "sound")).state == "unavailable"
    assert hass.states.get(_entity(hass, "select", "coffee_temperature")).state == "unavailable"


def test_a_malformed_cloud_message_is_skipped_not_fatal() -> None:
    def broken(*_: Any) -> None:
        raise ValueError("bad field")

    connection = DaedalusCloudConnection(
        None, "thing", AsyncMock(), broken, on_machine_message=broken  # type: ignore[arg-type]
    )
    connection._handle("thing/commands/response", b'{"Message": "BeverageRequestReport", "RecipeId": "x"}')
    connection._handle(
        "$aws/things/thing/shadow/name/MachineStatus/get/accepted",
        b'{"state": {"reported": {"Status": "Idle"}}}',
    )
