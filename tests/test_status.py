"""Tests for reading the machine's status reports and showing them in Home Assistant."""

from __future__ import annotations

from collections.abc import Callable
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.delonghi_daedalus.api.models import (
    DaedalusAppliance,
    MachineStatus,
)
from custom_components.delonghi_daedalus.api.websocket import DaedalusCommandError
from custom_components.delonghi_daedalus.const import (
    CONF_DEVICE_IP,
    CONF_PASSWORD,
    CONF_SERIAL_NUMBER,
    CONF_THING_NAME,
    CONF_USERNAME,
    DOMAIN,
)

SERIAL = "SN0001234567"


def _report(status: str, mode: int, **alarms: bool) -> dict[str, Any]:
    """A StatusService message shaped exactly like the ECAM 470.85's (2026-11-04)."""
    all_alarms = dict.fromkeys(
        "TankLevel Waste Descale Filter GroundFine General HeaterNTC Imbocco MotorPos Drain "
        "Bubble TankPresence Clean BrokenGrinder NoCoffee Infuser UIMemory WifiDown Fan "
        "BrewUnitClean Fridge".split(),
        False,
    )
    all_alarms.update(alarms)
    body = {
        "CurrMode": mode,
        "CurrFuncProgress": 0,
        "ExitBeverageWithError": False,
        "BeverageDispensing": 0,
        "accessories": {"None": False, "Water": True, "SteamWand": False},
        "peripherals": {"Switch": True, "WaterSpout": True, "WaterTankLow": False},
        "alarms": all_alarms,
        "StepDescr": "Unknown",
        "Status": status,
    }
    # The machine sends the report body as JSON text inside the message.
    return {"Message": "StatusService", "Response": json.dumps(body)}


def test_standby_report() -> None:
    status = MachineStatus.from_message(_report("Standby", 0, Filter=True))
    assert status is not None
    assert not status.is_on
    assert status.display_status == "Standby"
    assert status.alarms["Filter"] is True
    assert status.alarms["TankLevel"] is False


def test_idle_means_ready() -> None:
    status = MachineStatus.from_message(_report("Idle", 7))
    assert status is not None
    assert status.is_on
    assert status.display_status == "Ready"


def test_unknown_status_word_is_shown_as_sent() -> None:
    status = MachineStatus.from_message(_report("Rinsing", 3))
    assert status is not None
    assert status.display_status == "Rinsing"


@pytest.mark.parametrize(
    "message",
    [
        {"Message": "MachineUIResponse", "Response": "OK"},
        {"Message": "StatusService", "Response": "not json"},
        {"Message": "StatusService", "Response": "{}"},
    ],
)
def test_other_messages_are_not_status(message: dict[str, Any]) -> None:
    assert MachineStatus.from_message(message) is None


class StubLocal:
    """Stands in for the local connection so tests can push messages in."""

    def __init__(self, **_: Any) -> None:
        self.callbacks: list[Callable[[dict[str, Any]], None]] = []
        self.is_connected = True
        self.async_turn_on = AsyncMock(return_value={"Response": "OK"})

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


@pytest.fixture
async def machine(hass: HomeAssistant):
    """Set up the integration with a stubbed cloud and local connection."""
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
    stubs: list[StubLocal] = []

    def make_local(**kwargs):
        stubs.append(StubLocal(**kwargs))
        return stubs[-1]

    appliance = DaedalusAppliance("thing", SERIAL, "ECAM47080", "", "ONLINE")
    with (
        patch("custom_components.delonghi_daedalus.DaedalusCloudClient.async_authenticate"),
        patch(
            "custom_components.delonghi_daedalus.DaedalusCloudClient.async_get_devices",
            return_value=[appliance],
        ),
        patch(
            "custom_components.delonghi_daedalus.coordinator.DaedalusLocalWebSocket",
            side_effect=make_local,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield stubs[0]
        assert await hass.config_entries.async_unload(entry.entry_id)


def _state(hass: HomeAssistant, platform: str, key: str):
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{SERIAL}_{key}")
    assert entity_id, f"no {platform} entity for {key}"
    return hass.states.get(entity_id)


async def test_status_report_drives_entities(hass: HomeAssistant, machine: StubLocal) -> None:
    # Before any report, alarms are unknown rather than a reassuring "OK".
    assert _state(hass, "binary_sensor", "alarm_water_tank_empty").state == "unknown"

    machine.push(_report("Standby", 0, Filter=True))
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "status").state == "Standby"
    assert _state(hass, "switch", "power").state == "off"
    assert _state(hass, "binary_sensor", "alarm_water_filter").state == "on"
    assert _state(hass, "binary_sensor", "alarm_water_tank_empty").state == "off"

    machine.push(_report("Idle", 7, TankLevel=True))
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "status").state == "Ready"
    assert _state(hass, "switch", "power").state == "on"
    assert _state(hass, "binary_sensor", "alarm_water_tank_empty").state == "on"


async def test_technical_alarms_are_off_by_default(hass: HomeAssistant, machine: StubLocal) -> None:
    entry = er.async_get(hass).async_get(
        er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, f"{SERIAL}_alarm_fan")
    )
    assert entry is not None and entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_declined_command_shows_a_clear_error(hass: HomeAssistant, machine: StubLocal) -> None:
    machine.async_turn_on.side_effect = DaedalusCommandError("rejected", "KO")
    switch = _state(hass, "switch", "power")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": switch.entity_id}, blocking=True
        )
    assert err.value.translation_key == "command_rejected"
    # A declined command doesn't flip the switch.
    assert _state(hass, "switch", "power").state == switch.state
