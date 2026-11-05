"""Tests for reading the machine's status reports and showing them in Home Assistant."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.delonghi_daedalus.api.models import (
    DaedalusAppliance,
    MachineStatus,
)
from custom_components.delonghi_daedalus.api.websocket import DaedalusCommandError
from tests.helpers import SERIAL, StubLocal, entry_data
from custom_components.delonghi_daedalus.const import DOMAIN



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


@pytest.fixture
async def machine(hass: HomeAssistant):
    """Set up the integration with a stubbed cloud and local connection."""
    entry = MockConfigEntry(domain=DOMAIN, unique_id=SERIAL, data=entry_data())
    entry.add_to_hass(hass)
    stubs: list[StubLocal] = []

    def make_local(**kwargs):
        stubs.append(StubLocal(**kwargs))
        return stubs[-1]

    appliance = DaedalusAppliance("thing", SERIAL, "ECAM47080", "", "ONLINE")
    with (
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
    # Before any report nothing is guessed: no reassuring "OK", no made-up "Standby".
    assert _state(hass, "binary_sensor", "alarm_water_tank_empty").state == "unknown"
    assert _state(hass, "sensor", "status").state == "unknown"
    assert _state(hass, "switch", "power").state == "unknown"
    # Unknown state: no drinks until the machine is known to be ready (safety).
    assert _state(hass, "button", "drink_33").state == "unavailable"

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


async def test_cup_warmer_switch_is_off_by_default(hass: HomeAssistant, machine: StubLocal) -> None:
    """Not every model has a warming plate, and the machine doesn't say which do."""
    registry = er.async_get(hass)
    entry = registry.async_get(registry.async_get_entity_id("switch", DOMAIN, f"{SERIAL}_cup_warmer"))
    assert entry is not None and entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    sound = registry.async_get(registry.async_get_entity_id("switch", DOMAIN, f"{SERIAL}_sound"))
    assert sound is not None and sound.disabled_by is None


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


async def test_cloud_records_fill_status_settings_and_device(
    hass: HomeAssistant, machine: StubLocal, stub_cloud
) -> None:
    """Records from the cloud fill status, settings and the device page; local reports still win."""
    cloud = stub_cloud.instances[0]
    assert cloud.thing_name == "thing"

    cloud.on_shadow("MachineStatus", json.loads(_report("Idle", 7, Filter=True)["Response"]))
    cloud.on_shadow(
        "MachineSettings",
        {
            "Editable": {"CoffeeTemp": 1, "AutoTurnOffTimeInMin": 180, "WaterHardness": 0,
                         "SetProfile": 2, "UserConf": {"SoundEnable": False}},
            "NotEditable": {"DecalPerc": 49, "FilterUsagePerc": 126, "GroundCntPerc": 62,
                            "CoffeeGroundsCnt": 351, "WaterTotQty": 230863},
        },
    )
    cloud.on_shadow(
        "MachineCapabilities",
        {"SKU": "0132250187", "MAC": "C0:FF:EE:00:00:01", "FWUIVersion": "1.1.15",
         "FWWiFiVersion": "1.1.25", "FWMainboardVersion": "1.0.0",
         "CoffeeTemperatures": [0, 1, 2], "AutoTurnOffTimeInMinutes": [15, 30, 60, 180]},
    )
    await hass.async_block_till_done()

    assert _state(hass, "sensor", "status").state == "Ready"
    assert _state(hass, "switch", "power").state == "on"
    assert _state(hass, "select", "coffee_temperature").state == "medium"
    assert _state(hass, "select", "auto_off_timer").state == "180"
    assert _state(hass, "select", "coffee_temperature").attributes["options"] == ["low", "medium", "high"]
    assert _state(hass, "switch", "sound").state == "off"
    assert _state(hass, "sensor", "active_profile").state == "2"
    assert _state(hass, "sensor", "water_filter_used").state == "126"
    assert _state(hass, "sensor", "grounds_container").state == "62"

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.sw_version == "1.1.15"
    assert device.model_id == "0132250187"
    assert (dr.CONNECTION_NETWORK_MAC, "c0:ff:ee:00:00:01") in device.connections

    machine.push(_report("Standby", 0))
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "status").state == "Standby"



SETTINGS_RECORD = {
    "Editable": {
        "CoffeeTemp": 1,
        "AutoTurnOffTimeInMin": 180,
        "WaterHardness": 0,
        "SetProfile": 2,
        "UserConf": {"SoundEnable": False, "CupWarmer": True, "FilterInstall": True},
    },
    "NotEditable": {},
}


async def test_settings_commands_use_the_confirmed_format(
    hass: HomeAssistant, machine: StubLocal, stub_cloud
) -> None:
    stub_cloud.instances[0].on_shadow("MachineSettings", SETTINGS_RECORD)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "select",
        "select_option",
        {"entity_id": _state(hass, "select", "coffee_temperature").entity_id, "option": "high"},
        blocking=True,
    )
    machine.async_set_settings.assert_awaited_with({"CoffeeTemp": 2})
    assert _state(hass, "select", "coffee_temperature").state == "high"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": _state(hass, "switch", "sound").entity_id}, blocking=True
    )
    # The whole group goes out, only sound changed, so nothing else can be reset.
    machine.async_set_settings.assert_awaited_with(
        {"UserConf": {"SoundEnable": True, "CupWarmer": True, "FilterInstall": True}}
    )
    assert _state(hass, "switch", "sound").state == "on"


async def test_brewing_uses_the_active_profile(
    hass: HomeAssistant, machine: StubLocal, stub_cloud
) -> None:
    stub_cloud.instances[0].on_shadow("MachineSettings", SETTINGS_RECORD)
    machine.push(_report("Idle", 7))
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button", "press", {"entity_id": _state(hass, "button", "drink_33").entity_id}, blocking=True
    )
    machine.async_brew.assert_awaited_once_with(33, 2, None)



async def test_brew_progress_and_drinks_made_in_the_app(
    hass: HomeAssistant, machine: StubLocal, stub_cloud
) -> None:
    """Live progress while busy; the cloud's brew report fills the last drink."""
    cloud = stub_cloud.instances[0]
    busy = json.loads(_report("Busy", 11)["Response"]) | {"BeverageDispensing": 42}
    cloud.on_shadow("MachineStatus", busy)
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "brew_progress").state == "42"
    assert _state(hass, "sensor", "status").state == "Busy"

    # The report the machine sent for a hot water made in the app (2026-11-04).
    stub_cloud.instances[0].kwargs_on_message(
        {"Message": "BeverageRequestReport", "Response": "OK", "AppId": "MCL",
         "RecipeId": 16, "ProfileId": 2, "WaterQty2": 303, "Rinse": 1}
    )
    cloud.on_shadow("MachineStatus", json.loads(_report("Idle", 7)["Response"]))
    await hass.async_block_till_done()
    last = _state(hass, "sensor", "last_beverage")
    assert last.attributes["recipe_id"] == 16
    assert last.attributes["water_ml"] == 303
    assert _state(hass, "sensor", "brew_progress").state == "0"



async def test_brew_profile_choice(hass: HomeAssistant, machine: StubLocal, stub_cloud) -> None:
    """Starts at the machine's profile; a choice in Home Assistant goes with each drink."""
    stub_cloud.instances[0].on_shadow("MachineSettings", SETTINGS_RECORD)
    await hass.async_block_till_done()
    profile = _state(hass, "select", "brew_profile")
    assert profile.state == "2"
    assert profile.attributes["options"] == ["2"]  # only profiles known to exist

    # A drink made in the app with profile 3 shows that profile exists.
    stub_cloud.instances[0].kwargs_on_message(
        {"Message": "BeverageRequestReport", "Response": "OK", "RecipeId": 1, "ProfileId": 3}
    )
    await hass.async_block_till_done()
    assert _state(hass, "select", "brew_profile").attributes["options"] == ["2", "3"]

    await hass.services.async_call(
        "select", "select_option", {"entity_id": profile.entity_id, "option": "3"}, blocking=True
    )
    machine.push(_report("Idle", 7))
    await hass.async_block_till_done()
    await hass.services.async_call(
        "button", "press", {"entity_id": _state(hass, "button", "drink_1").entity_id}, blocking=True
    )
    machine.async_brew.assert_awaited_once_with(1, 3, None)


def _filter_record(count: int, usage: int) -> dict:
    return {"Editable": {"SetProfile": 2}, "NotEditable": {"FilterTotCnt": count, "FilterUsagePerc": usage}}


async def test_filter_change_date_is_recorded_from_the_machine(
    hass: HomeAssistant, machine: StubLocal, stub_cloud, freezer
) -> None:
    cloud = stub_cloud.instances[0]
    changed = lambda: _state(hass, "sensor", "water_filter_changed").state  # noqa: E731

    # First reading: only a starting point, no made-up date.
    cloud.on_shadow("MachineSettings", _filter_record(2, 127))
    await hass.async_block_till_done()
    assert changed() == "unknown"

    # Normal use doesn't count as a change.
    cloud.on_shadow("MachineSettings", _filter_record(2, 128))
    await hass.async_block_till_done()
    assert changed() == "unknown"

    # A new filter registered on the machine: the count goes up, usage resets.
    freezer.move_to("2026-11-05 08:30:00+00:00")
    cloud.on_shadow("MachineSettings", _filter_record(3, 0))
    await hass.async_block_till_done()
    assert changed() == "2026-11-05T08:30:00+00:00"

    # A machine that resets the percentage without counting is caught too.
    cloud.on_shadow("MachineSettings", _filter_record(3, 64))
    freezer.move_to("2027-01-05 09:00:00+00:00")
    cloud.on_shadow("MachineSettings", _filter_record(3, 1))
    await hass.async_block_till_done()
    assert changed() == "2027-01-05T09:00:00+00:00"


async def test_filter_change_date_survives_a_reload(
    hass: HomeAssistant, machine: StubLocal, stub_cloud, freezer
) -> None:
    freezer.move_to("2026-11-05 08:30:00+00:00")
    cloud = stub_cloud.instances[0]
    cloud.on_shadow("MachineSettings", _filter_record(2, 127))
    cloud.on_shadow("MachineSettings", _filter_record(3, 0))
    await hass.async_block_till_done()

    # Reload straight away, inside the store's save delay.
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _state(hass, "sensor", "water_filter_changed").state == "2026-11-05T08:30:00+00:00"
