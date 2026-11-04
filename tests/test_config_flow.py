"""Tests for the sign-in screens: first-time setup and signing in again."""

from __future__ import annotations

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
    CONF_DEVICE_IP,
    CONF_PASSWORD,
    CONF_SERIAL_NUMBER,
    CONF_THING_NAME,
    CONF_USERNAME,
    DOMAIN,
)

CLIENT = "custom_components.delonghi_daedalus.config_flow.DaedalusCloudClient"
SEARCH = "custom_components.delonghi_daedalus.config_flow.async_find_local_machine"
EMAIL = "user@example.com"
SERIAL = "SN0001234567"


def _machine(serial: str = SERIAL) -> DaedalusAppliance:
    return DaedalusAppliance(
        machine_name=f"thing-{serial}", serial_number=serial, model="ECAM470", sku="", status="ONLINE"
    )


def _cloud(*, authenticate=None, devices=None):
    """Patch the cloud client the setup screens use."""
    mock = patch(CLIENT, autospec=True)
    client_cls = mock.start()
    client = client_cls.return_value
    client.async_authenticate = AsyncMock(side_effect=authenticate)
    client.async_get_devices = AsyncMock(return_value=devices if devices is not None else [_machine()])
    return mock, client


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    """A machine that was set up earlier with a password that has since changed."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERIAL,
        title="De'Longhi Eletta Ultra (234567)",
        data={
            CONF_USERNAME: EMAIL,
            CONF_PASSWORD: "old-password",
            CONF_SERIAL_NUMBER: SERIAL,
            CONF_THING_NAME: f"thing-{SERIAL}",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_creates_entry(hass: HomeAssistant) -> None:
    """With no address typed in, the machine is found on the network."""
    mock, client = _cloud()
    try:
        with patch(SEARCH, return_value=("192.0.2.8", b"fp")) as search:
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_USERNAME: f"  {EMAIL} ", CONF_PASSWORD: "pw"}
            )
    finally:
        mock.stop()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_USERNAME] == EMAIL
    assert result["data"][CONF_DEVICE_IP] == "192.0.2.8"
    assert result["result"].unique_id == SERIAL
    client.async_authenticate.assert_awaited_once_with(EMAIL, "pw")
    assert search.call_args.args[1] == SERIAL


async def test_setup_with_address_skips_the_search(hass: HomeAssistant) -> None:
    mock, _ = _cloud()
    try:
        with patch(SEARCH) as search:
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {CONF_USERNAME: EMAIL, CONF_PASSWORD: "pw", CONF_DEVICE_IP: "192.0.2.9"},
            )
    finally:
        mock.stop()

    assert result["data"][CONF_DEVICE_IP] == "192.0.2.9"
    search.assert_not_called()


async def test_machine_not_found_asks_for_address(hass: HomeAssistant) -> None:
    mock, _ = _cloud()
    try:
        with patch(SEARCH, return_value=None):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": config_entries.SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_USERNAME: EMAIL, CONF_PASSWORD: "pw"}
            )
            assert result["type"] is FlowResultType.FORM
            assert result["step_id"] == "local_address"
            # Leaving it empty still sets up, just without local control for now.
            result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    finally:
        mock.stop()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE_IP] is None


async def test_setup_wrong_password_keeps_email(hass: HomeAssistant) -> None:
    """The form comes back with the error and the email still filled in, password cleared."""
    mock, _ = _cloud(authenticate=DaedalusAuthError("403042"))
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: EMAIL, CONF_PASSWORD: "wrong"}
        )
    finally:
        mock.stop()

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in result["data_schema"].schema
        if key.description
    }
    assert suggested.get(CONF_USERNAME) == EMAIL
    assert CONF_PASSWORD not in suggested


async def test_setup_locked_account_says_so(hass: HomeAssistant) -> None:
    mock, _ = _cloud(authenticate=DaedalusAccountLockedError("403120"))
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: EMAIL, CONF_PASSWORD: "pw"}
        )
    finally:
        mock.stop()

    assert result["errors"] == {"base": "account_locked"}


async def test_setup_offline_is_cannot_connect(hass: HomeAssistant) -> None:
    mock, _ = _cloud(authenticate=DaedalusConnectionError("timeout"))
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: EMAIL, CONF_PASSWORD: "pw"}
        )
    finally:
        mock.stop()

    assert result["errors"] == {"base": "cannot_connect"}


async def test_reauth_updates_password(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    mock, client = _cloud()
    try:
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "reauth_confirm"
        assert result["description_placeholders"]["username"] == EMAIL

        with patch(
            "custom_components.delonghi_daedalus.async_setup_entry", return_value=True
        ):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_PASSWORD: "new-password"}
            )
    finally:
        mock.stop()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-password"
    client.async_authenticate.assert_awaited_once_with(EMAIL, "new-password")


async def test_reauth_wrong_password_stays_on_form(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    mock, _ = _cloud(authenticate=DaedalusAuthError("403042"))
    try:
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "still-wrong"}
        )
    finally:
        mock.stop()

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data[CONF_PASSWORD] == "old-password"


async def test_reauth_machine_not_on_account(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    mock, _ = _cloud(devices=[_machine("SOMEONE-ELSES")])
    try:
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new-password"}
        )
    finally:
        mock.stop()

    assert result["errors"] == {"base": "device_not_on_account"}
    assert entry.data[CONF_PASSWORD] == "old-password"


async def test_rejected_password_at_startup_asks_to_sign_in_again(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    """Home Assistant shows the sign-in-again prompt instead of a dead integration."""
    with patch(
        "custom_components.delonghi_daedalus.DaedalusCloudClient.async_authenticate",
        side_effect=DaedalusAuthError("403042"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [config_entries.SOURCE_REAUTH]


async def test_cloud_down_at_startup_retries(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    with patch(
        "custom_components.delonghi_daedalus.DaedalusCloudClient.async_authenticate",
        side_effect=DaedalusConnectionError("timeout"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
