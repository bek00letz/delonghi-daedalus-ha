"""Tests for the setup, sign-in-again and options screens, including confirming the machine."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.delonghi_daedalus.api.client import (
    DaedalusAccountLockedError,
    DaedalusAuthError,
    DaedalusConnectionError,
)
from custom_components.delonghi_daedalus.api.models import DaedalusAppliance
from custom_components.delonghi_daedalus.const import (
    CONF_CONFIRM_MACHINE,
    CONF_DEVICE_IP,
    CONF_FINGERPRINT_HANDOFF,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_SESSION_HANDOFF,
    CONF_TOKEN_HANDOFF,
    CONF_USERNAME,
    DOMAIN,
)
from tests.helpers import DAY, FINGERPRINT, SERIAL, StubLocal, entry_data, make_token

FLOW = "custom_components.delonghi_daedalus.config_flow"
CLOUD = "custom_components.delonghi_daedalus.DaedalusCloudClient"
EMAIL = "user@example.com"
MACHINE_AT, OTHER_AT = "192.0.2.8", "192.0.2.9"
MACHINE_FP, OTHER_FP = b"\x01" * 32, b"\x02" * 32


def _machine(serial: str = SERIAL) -> DaedalusAppliance:
    return DaedalusAppliance(f"thing-{serial}", serial, "ECAM470", "", "ONLINE")


class FakeClient:
    """Stands in for the cloud client on the setup screens."""

    sign_in_error: Exception | None = None
    devices: list[DaedalusAppliance] = []
    sign_ins: list[tuple[str, str]] = []

    def __init__(self, session: Any = None, **_: Any) -> None:
        self.gigya_session = ("session-token", "session-secret")
        self._jwt_token = "account-token"

    async def async_sign_in(self, username: str, password: str) -> None:
        FakeClient.sign_ins.append((username, password))
        if FakeClient.sign_in_error:
            raise FakeClient.sign_in_error

    async def async_get_devices(self) -> list[DaedalusAppliance]:
        return FakeClient.devices

    async def async_get_token(self, refresh: bool = False) -> str:
        return self._jwt_token


@pytest.fixture
def cloud():
    FakeClient.sign_in_error = None
    FakeClient.devices = [_machine()]
    FakeClient.sign_ins = []
    with patch(f"{FLOW}.DaedalusCloudClient", FakeClient):
        yield FakeClient


@pytest.fixture
def network():
    """The home network: two devices that look like coffee machines; the machine reports .8."""
    accepts = AsyncMock(side_effect=lambda session, host, fp, serial, token: host == MACHINE_AT)
    with (
        patch(
            f"{FLOW}.async_find_local_candidates",
            AsyncMock(return_value=[(OTHER_AT, OTHER_FP), (MACHINE_AT, MACHINE_FP)]),
        ) as scan,
        patch(f"{FLOW}.async_find_candidates", AsyncMock(return_value=[(MACHINE_AT, MACHINE_FP)])) as one,
        patch(f"{FLOW}.async_read_reported_address", AsyncMock(return_value=MACHINE_AT)),
        patch(f"{FLOW}.async_accepts_token", accepts),
    ):
        yield {"scan": scan, "one": one, "accepts": accepts}


async def _past_progress(hass: HomeAssistant, result: dict, manager: Any) -> dict:
    """Let the "Looking for your coffee machine" step finish, then carry on."""
    while result["type"] is FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await manager.async_configure(result["flow_id"])
    return result


async def _start(hass: HomeAssistant, **form: Any) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: f"  {EMAIL} ", CONF_PASSWORD: "pw", **form}
    )
    return await _past_progress(hass, result, hass.config_entries.flow)


async def test_setup_confirms_the_machine_before_sending_the_token(
    hass: HomeAssistant, cloud, network
) -> None:
    result = await _start(hass)
    assert result["step_id"] == "confirm_machine"
    # Nothing was sent to any device yet; the cloud-reported address is listed first.
    network["accepts"].assert_not_awaited()
    assert result["description_placeholders"]["reported"] == MACHINE_AT
    schema_default = next(iter(result["data_schema"].schema)).default()
    assert schema_default == MACHINE_AT

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"machine": MACHINE_AT})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # The token went to the confirmed device only, pinned to its certificate.
    network["accepts"].assert_awaited_once()
    assert network["accepts"].await_args.args[1:3] == (MACHINE_AT, MACHINE_FP)
    data = result["data"]
    assert data[CONF_USERNAME] == EMAIL
    assert data[CONF_DEVICE_IP] == MACHINE_AT
    assert data[CONF_FINGERPRINT_HANDOFF] == MACHINE_FP.hex()
    assert data[CONF_SESSION_HANDOFF] == ["session-token", "session-secret"]
    assert CONF_PASSWORD not in data  # the password is never stored
    assert cloud.sign_ins == [(EMAIL, "pw")]


async def test_a_device_that_refuses_isnt_trusted(hass: HomeAssistant, cloud, network) -> None:
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"machine": OTHER_AT})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "machine_refused"}


async def test_typed_address_is_checked_and_only_that_device_is_looked_at(
    hass: HomeAssistant, cloud, network
) -> None:
    result = await _start(hass, **{CONF_DEVICE_IP: "evil.example.com:443"})
    assert result["errors"] == {CONF_DEVICE_IP: "invalid_address"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: EMAIL, CONF_PASSWORD: "pw", CONF_DEVICE_IP: MACHINE_AT}
    )
    result = await _past_progress(hass, result, hass.config_entries.flow)
    assert result["step_id"] == "confirm_machine"
    network["one"].assert_awaited_once_with([MACHINE_AT])
    network["scan"].assert_not_awaited()


async def test_nothing_found_can_continue_without_local_control(hass: HomeAssistant, cloud) -> None:
    with (
        patch(f"{FLOW}.async_find_local_candidates", AsyncMock(return_value=[])),
        patch(f"{FLOW}.async_read_reported_address", AsyncMock(return_value=None)),
        patch(f"{FLOW}.async_accepts_token", AsyncMock()) as accepts,
    ):
        result = await _start(hass)
        assert result["step_id"] == "local_address"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_IP] is None
    assert CONF_FINGERPRINT_HANDOFF not in result["data"]
    accepts.assert_not_awaited()


async def test_wrong_password_keeps_the_email(hass: HomeAssistant, cloud) -> None:
    cloud.sign_in_error = DaedalusAuthError("403042")
    result = await _start(hass)
    assert result["errors"] == {"base": "invalid_auth"}
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested.get(CONF_USERNAME) == EMAIL.strip() or suggested.get(CONF_USERNAME) == f"  {EMAIL} "
    assert CONF_PASSWORD not in suggested


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        (DaedalusAccountLockedError("403120"), "account_locked"),
        (DaedalusConnectionError("timeout"), "cannot_connect"),
    ],
)
async def test_sign_in_problems_are_named(hass: HomeAssistant, cloud, error, shown) -> None:
    cloud.sign_in_error = error
    result = await _start(hass)
    assert result["errors"] == {"base": shown}


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    data = entry_data()
    for key in (CONF_SESSION_HANDOFF, CONF_TOKEN_HANDOFF, CONF_FINGERPRINT_HANDOFF):
        data.pop(key)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, title="Eletta", data={**data, CONF_PASSWORD: "old"})
    entry.add_to_hass(hass)
    return entry


async def test_reauth_keeps_the_session_and_drops_any_password(
    hass: HomeAssistant, cloud, entry: MockConfigEntry
) -> None:
    result = await entry.start_reauth_flow(hass)
    assert result["description_placeholders"]["username"] == EMAIL
    with patch("custom_components.delonghi_daedalus.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new"})
    assert result["reason"] == "reauth_successful"
    assert CONF_PASSWORD not in entry.data
    assert entry.data[CONF_SESSION_HANDOFF] == ["session-token", "session-secret"]


async def test_reauth_wrong_password_stays_on_form(hass: HomeAssistant, cloud, entry) -> None:
    cloud.sign_in_error = DaedalusAuthError("403042")
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "x"})
    assert result["errors"] == {"base": "invalid_auth"}


async def test_reauth_machine_not_on_account(hass: HomeAssistant, cloud, entry) -> None:
    cloud.devices = [_machine("SOMEONE-ELSES")]
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PASSWORD: "new"})
    assert result["errors"] == {"base": "device_not_on_account"}


async def test_refused_session_at_startup_asks_to_sign_in_again(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data=entry_data(**{CONF_TOKEN_HANDOFF: make_token(5 * DAY)}))
    entry.add_to_hass(hass)
    with patch(f"{CLOUD}.async_get_jwt", AsyncMock(side_effect=DaedalusAuthError("403005"))):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [config_entries.SOURCE_REAUTH]


async def test_cloud_down_without_a_valid_token_retries(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data=entry_data(**{CONF_TOKEN_HANDOFF: make_token(-DAY)}))
    entry.add_to_hass(hass)
    with patch(f"{CLOUD}.async_get_jwt", AsyncMock(side_effect=DaedalusConnectionError("down"))):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_confirming_the_machine_again_pins_it_and_reloads(
    hass: HomeAssistant, hass_storage: dict[str, Any], network
) -> None:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data=entry_data())
    entry.add_to_hass(hass)
    locals_: list[StubLocal] = []
    with (
        patch(f"{CLOUD}.async_get_devices", AsyncMock(return_value=[_machine()])),
        patch(
            "custom_components.delonghi_daedalus.coordinator.DaedalusLocalWebSocket",
            side_effect=lambda **kw: locals_.append(StubLocal(**kw)) or locals_[-1],
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert locals_[-1].kwargs["fingerprint"] == FINGERPRINT

        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {CONF_POLL_INTERVAL: 30, CONF_CONFIRM_MACHINE: True}
        )
        result = await _past_progress(hass, result, hass.config_entries.options)
        assert result["step_id"] == "confirm_machine"
        result = await hass.config_entries.options.async_configure(result["flow_id"], {"machine": MACHINE_AT})
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_DEVICE_IP] == MACHINE_AT
    # Reloaded straight away, now trusting the newly confirmed machine.
    assert locals_[-1].kwargs["fingerprint"] == MACHINE_FP
    assert locals_[-1].kwargs["host"] == MACHINE_AT


async def test_no_confirmed_machine_means_no_local_connection(hass: HomeAssistant, hass_storage) -> None:
    data = entry_data()
    data.pop(CONF_FINGERPRINT_HANDOFF)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data=data)
    entry.add_to_hass(hass)
    with (
        patch(f"{CLOUD}.async_get_devices", AsyncMock(return_value=[_machine()])),
        patch("custom_components.delonghi_daedalus.coordinator.DaedalusLocalWebSocket") as local,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    local.assert_not_called()  # the token never goes to an unconfirmed device
    from homeassistant.helpers import issue_registry as ir

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"machine_not_confirmed_{SERIAL}") is not None
