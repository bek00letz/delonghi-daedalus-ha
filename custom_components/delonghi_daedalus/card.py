"""The drinks card: its script, and the list it reads.

The card is shipped inside the integration and loaded on every dashboard, so
there's nothing extra to install. It reads the drink list through Home
Assistant's signed-in connection (``delonghi_daedalus/drinks``) rather than
from the buttons, because buttons hide their photo while the machine can't
brew; brewing still goes through the buttons, so the same safety rule applies.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN

CARD_URL_BASE = f"/{DOMAIN}/card"
CARD_FILE = "delonghi-drinks-card.js"
CARD_FOLDER = Path(__file__).parent / "frontend"
# The machine's controls the card shows, by entity key, in the card's order.
CONTROLS = {
    "power": "switch",
    "brew_profile": "select",
    "coffee_temperature": "select",
    "auto_off_timer": "select",
    "water_hardness": "select",
    "sound": "switch",
    "energy_saving": "switch",
    "cup_warmer": "switch",
}


async def async_setup_card(hass: HomeAssistant) -> None:
    """Serve the card and its list, once per run."""
    done: set[str] = hass.data.setdefault(f"{DOMAIN}_web", set())
    if "card" in done or hass.http is None:
        return
    done.add("card")
    version = await hass.async_add_executor_job(_integration_version)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL_BASE, str(CARD_FOLDER), False)]
    )
    # Load the card on every dashboard. The version in the address makes
    # browsers fetch the new card after an update.
    if "frontend" in hass.config.components:
        frontend.add_extra_js_url(hass, f"{CARD_URL_BASE}/{CARD_FILE}?v={version}")
    websocket_api.async_register_command(hass, ws_drinks)


def _integration_version() -> str:
    manifest = json.loads((Path(__file__).parent / "manifest.json").read_text())
    return str(manifest.get("version", "0"))


def _entity_id(registry: er.EntityRegistry, platform: str, unique_id: str) -> str | None:
    return registry.async_get_entity_id(platform, DOMAIN, unique_id)


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/drinks"})
@callback
def ws_drinks(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Each machine with its drinks: name, photo, groups and the button that makes it."""
    registry = er.async_get(hass)
    machines = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is None:
            continue
        serial = coordinator.appliance.serial_number
        drinks = []
        for drink in coordinator.drinks.drinks:
            entity_id = _entity_id(registry, "button", f"{serial}_drink_{drink.id}")
            if entity_id is None:
                continue
            drinks.append(
                {
                    "id": drink.id,
                    "name": drink.name or coordinator.drinks.name(drink.id),
                    "groups": sorted(g.lower() for g in drink.groups),
                    "description": drink.description,
                    "photo": coordinator.drinks.photo(drink.id),
                    "entity_id": entity_id,
                }
            )
        controls = {
            key: entity_id
            for key, platform in CONTROLS.items()
            if (entity_id := _entity_id(registry, platform, f"{serial}_{key}"))
        }
        machines.append(
            {
                "entry_id": entry.entry_id,
                "name": coordinator.device_info.get("name"),
                "status_entity_id": _entity_id(registry, "sensor", f"{serial}_status"),
                "power_entity_id": controls.get("power"),
                "progress_entity_id": _entity_id(registry, "sensor", f"{serial}_brew_progress"),
                "controls": controls,
                "drinks": drinks,
            }
        )
    connection.send_result(msg["id"], {"machines": machines})
