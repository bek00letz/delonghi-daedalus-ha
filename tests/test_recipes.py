"""What a drink request carries: De'Longhi's values, then the machine's own, then saved changes."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from homeassistant.core import HomeAssistant

from custom_components.delonghi_daedalus import recipes
from custom_components.delonghi_daedalus.api.content import Drink, parse_drinks
from custom_components.delonghi_daedalus.api.websocket import DaedalusLocalWebSocket
from custom_components.delonghi_daedalus.const import DOMAIN
from tests.helpers import FINGERPRINT, SERIAL
from tests.test_drinks import _deliver_capabilities, _setup, entry  # noqa: F401  (fixture)


def _ingredient(name: str, low: int, default: int, high: int) -> dict:
    return {"name": name, "minval": str(low), "defval": str(default), "maxval": str(high)}


# Shaped like De'Longhi's list for an Eletta Ultra (seen 2026-11-05).
LIST = {"entries": [{"drinks": [
    {"title": "Espresso", "display": True, "drink": [{"drink_id": "1", "customized_title": "Espresso", "cluster": ["HOT", "COFFEE"],
     "ingredients": [_ingredient("Water", 20, 40, 80), _ingredient("Aroma", 0, 4, 6), _ingredient("Double", 0, 0, 1),
                     _ingredient("PowderQty", 105, 105, 105), _ingredient("AromaCMMorning", 0, 4, 6),
                     _ingredient("ExtraShotEnabledEvening", 0, 0, 1), _ingredient("BevProgMyFunct", 0, 0, 1)]}]},
    {"title": "Latte", "display": True, "drink": [{"drink_id": "8", "customized_title": "Latte Macchiato", "cluster": ["HOT", "COFFEE", "MILK"],
     "ingredients": [_ingredient("Water", 20, 60, 180), _ingredient("Aroma", 0, 3, 5), _ingredient("MilkQty", 50, 329, 790),
                     _ingredient("MilkFoam", 1, 1, 1), _ingredient("MilkQtyMlDef", 225, 225, 225)]}]},
    {"title": "Hot Water", "display": True, "drink": [{"drink_id": "16", "customized_title": "Hot Water", "cluster": ["HOT"],
     "ingredients": [_ingredient("WaterQty2", 20, 250, 420), _ingredient("Rinse", 0, 1, 1), _ingredient("Programmable", 1, 1, 1),
                     _ingredient("Visible", 1, 1, 1), _ingredient("LengthIndex", 0, 1, 4), _ingredient("Accessory", 1, 1, 1)]}]},
    {"title": "CustomBev1", "display": True, "drink": [{"drink_id": "230", "original_title": "CustomBev1", "cluster": ["HOT", "COFFEE", "CUSTOM"],
     "ingredients": [_ingredient("Water", 20, 0, 180), _ingredient("Aroma", 0, 255, 5)]}]},
]}]}
DRINKS = {d.id: d for d in parse_drinks(LIST)}


def test_factory_values_without_the_apps_extras() -> None:
    assert recipes.recipe(DRINKS[1], {}, {}) == {"Water": 40, "Aroma": 4, "Double": 0, "PowderQty": 105}


def test_the_machines_own_recipe_wins_over_the_factory_one() -> None:
    """Hot water as the app sends it for this person (their 303 ml, LengthIndex 48)."""
    reported = recipes.machine_values({
        "Message": "BeverageRequestReport", "Response": "OK", "AppId": "MCL", "RecipeId": 16, "ProfileId": 2,
        "WaterQty2": 303, "Rinse": 1, "Programmable": 1, "Visible": 1, "LengthIndex": 48,
    })
    assert recipes.recipe(DRINKS[16], reported, {}) == {
        "WaterQty2": 303, "Rinse": 1, "Programmable": 1, "Visible": 1, "LengthIndex": 48,
    }


def test_saved_changes_win_and_stay_within_limits() -> None:
    values = recipes.recipe(DRINKS[8], {"Aroma": 3, "IdPowderQty": 95}, {"Aroma": 9, "MilkQty": 219, "PowderQty": 1})
    assert values["Aroma"] == 5  # clamped to the drink's highest
    assert values["MilkQty"] == 219
    assert "IdPowderQty" not in values  # report-only field, not something to send
    assert "PowderQty" not in values  # not a value a person can change


def test_unset_custom_slot_values_arent_sent() -> None:
    assert recipes.recipe(DRINKS[230], {}, {}) == {}  # "255" means not set


def test_the_sheet_offers_only_what_can_change() -> None:
    latte = {c["key"]: c for c in recipes.controls(DRINKS[8], {"Aroma": 3, "MilkQty": 219}, {})}
    assert list(latte) == ["Aroma", "Water", "MilkQty"]  # foam is fixed for this drink
    assert latte["Aroma"]["usual"] == 3 and latte["Aroma"]["value"] == 3
    # Milk in the machine's units, with De'Longhi's own ml figure: 2 "units" ≈ 150 ml.
    assert round(latte["MilkQty"]["usual"] * latte["MilkQty"]["ml_per_unit"]) == 150
    espresso = {c["key"]: c["kind"] for c in recipes.controls(DRINKS[1], {}, {})}
    assert espresso == {"Aroma": "scale", "Water": "ml", "Double": "switch"}


def test_saving_keeps_only_real_changes() -> None:
    assert recipes.clean_personal(DRINKS[8], {"Aroma": 2.4, "MilkFoam": 2, "Nonsense": 1, "Water": "x"}) == {"Aroma": 2}


async def test_the_drink_request_carries_its_values_at_the_top() -> None:
    socket = DaedalusLocalWebSocket(
        host="192.0.2.10", serial_number=SERIAL, token_provider=AsyncMock(), port=443, session=None,  # type: ignore[arg-type]
        fingerprint=FINGERPRINT,
    )
    socket.async_send_command = AsyncMock(return_value={"Response": "OK"})  # type: ignore[method-assign]
    await socket.async_brew(16, 2, {"WaterQty2": 60, "Rinse": 1, "Message": "sneaky"})
    sent = socket.async_send_command.await_args.args[0]
    assert sent["Message"] == "BeverageRequest" and sent["RecipeId"] == 16 and sent["ProfileId"] == 2
    assert sent["WaterQty2"] == 60 and sent["Rinse"] == 1
    assert "Parameters" not in sent


@pytest.fixture
async def running(hass: HomeAssistant, entry, stub_cloud, stub_content):  # noqa: F811
    stub_content.drinks = list(DRINKS.values())
    machine = await _setup(hass, entry)
    await _deliver_capabilities(hass, stub_cloud)
    return entry, machine


async def test_a_drink_is_made_as_the_machine_last_made_it_then_with_changes(
    hass: HomeAssistant, running, hass_ws_client
) -> None:
    entry, machine = running
    coordinator = hass.data[DOMAIN][entry.entry_id]
    profile = coordinator.active_profile
    # The person makes a latte in the app; the machine reports how.
    machine.push({"Message": "BeverageRequestReport", "Response": "OK", "AppId": "MCL", "RecipeId": 8,
                  "ProfileId": profile, "Water": 72, "Aroma": 3, "MilkQty": 219, "IdPowderQty": 95})
    await hass.async_block_till_done()

    await coordinator.async_brew(8)
    assert machine.async_brew.await_args.args == (8, profile, {"Water": 72, "Aroma": 3, "MilkQty": 219, "MilkFoam": 1})

    # Stronger, from the card's sheet; then back to the machine's own.
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/drink_settings", "entry_id": entry.entry_id,
                            "drink_id": 8, "values": {"Aroma": 5}})
    reply = await client.receive_json()
    aroma = next(c for c in reply["result"]["controls"] if c["key"] == "Aroma")
    assert (aroma["value"], aroma["usual"]) == (5, 3)
    await coordinator.async_brew(8)
    assert machine.async_brew.await_args.args[2]["Aroma"] == 5

    await client.send_json({"id": 2, "type": f"{DOMAIN}/drink_settings", "entry_id": entry.entry_id,
                            "drink_id": 8, "values": None})
    await client.receive_json()
    await coordinator.async_brew(8)
    assert machine.async_brew.await_args.args[2]["Aroma"] == 3

    await client.send_json({"id": 3, "type": f"{DOMAIN}/drink_settings", "entry_id": entry.entry_id,
                            "drink_id": 999, "values": {"Aroma": 1}})
    assert (await client.receive_json())["error"]["code"] == "not_found"


async def test_the_card_gets_each_drinks_controls(hass: HomeAssistant, running, hass_ws_client) -> None:
    entry, machine = running
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/drinks"})
    drinks = {d["id"]: d for d in (await client.receive_json())["result"]["machines"][0]["drinks"]}
    assert [c["key"] for c in drinks[1]["controls"]] == ["Aroma", "Water", "Double"]
    assert drinks[16]["controls"][0]["label"] == "Water"
    assert json.dumps(drinks)  # all plain data
