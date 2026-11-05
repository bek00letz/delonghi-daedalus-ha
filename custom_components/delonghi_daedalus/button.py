"""One button per drink the machine offers, from De'Longhi's drink list."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api.content import Drink
from .const import DOMAIN
from .coordinator import DaedalusCoordinator
from .entity import DaedalusEntity

# Buttons of 0.7 and earlier, moved to their drink numbers so their entity IDs
# (and automations using them) carry over.
OLD_BUTTON_KEYS = {
    "brew_espresso": 1,
    "brew_coffee": 2,
    "brew_long": 3,
    "brew_doppio_plus": 5,
    "brew_americano": 6,
    "brew_hot_water": 16,
    "brew_coffee_xl": 33,
}


def drink_key(drink_id: int) -> str:
    return f"drink_{drink_id}"


def drink_icon(drink: Drink) -> str:
    """An icon for places that don't show the photo."""
    groups = drink.groups
    if drink.is_mug:
        return "mdi:coffee-to-go"
    if "COLD" in groups:
        return "mdi:snowflake"
    if "MILK" in groups and "COFFEE" not in groups:
        return "mdi:cup"
    if "COFFEE" not in groups:
        return "mdi:cup-water"
    return "mdi:coffee"


@callback
def _move_old_buttons(hass: HomeAssistant, coordinator: DaedalusCoordinator) -> None:
    registry = er.async_get(hass)
    serial = coordinator.appliance.serial_number
    for old_key, drink_id in OLD_BUTTON_KEYS.items():
        entity_id = registry.async_get_entity_id("button", DOMAIN, f"{serial}_{old_key}")
        new_unique_id = f"{serial}_{drink_key(drink_id)}"
        if entity_id and not registry.async_get_entity_id("button", DOMAIN, new_unique_id):
            registry.async_update_entity(entity_id, new_unique_id=new_unique_id)


@callback
def _forget_missing_drinks(hass: HomeAssistant, entry: ConfigEntry, coordinator: DaedalusCoordinator) -> None:
    """Remove buttons for drinks De'Longhi's list says this machine doesn't make."""
    if not coordinator.drinks.from_delonghi:
        return
    registry = er.async_get(hass)
    prefix = f"{coordinator.appliance.serial_number}_drink_"
    offered = {drink_key(d.id) for d in coordinator.drinks.drinks}
    for entry_ in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entry_.domain != "button" or not entry_.unique_id.startswith(prefix):
            continue
        if entry_.unique_id.removeprefix(f"{coordinator.appliance.serial_number}_") not in offered:
            registry.async_remove(entry_.entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add a button per drink, and new ones whenever the drink list grows."""
    coordinator: DaedalusCoordinator = hass.data[DOMAIN][entry.entry_id]
    _move_old_buttons(hass, coordinator)
    added: set[int] = set()

    @callback
    def add_new_drinks() -> None:
        new = [d for d in coordinator.drinks.drinks if d.id not in added]
        added.update(d.id for d in new)
        _forget_missing_drinks(hass, entry, coordinator)
        if new:
            async_add_entities(DaedalusDrinkButton(coordinator, d.id) for d in new)

    add_new_drinks()
    entry.async_on_unload(coordinator.drinks.add_listener(add_new_drinks))


class DaedalusDrinkButton(DaedalusEntity, ButtonEntity):
    """Makes one drink."""

    def __init__(self, coordinator: DaedalusCoordinator, drink_id: int) -> None:
        super().__init__(coordinator, drink_key(drink_id))
        self._drink_id = drink_id
        drink = coordinator.drinks.get(drink_id)
        # Main drinks start switched on; mug versions and custom slots start off.
        self._attr_entity_registry_enabled_default = bool(drink and drink.is_main)
        self._attr_translation_key = None
        self._update_from_list()

    @callback
    def _update_from_list(self) -> None:
        drink = self.coordinator.drinks.get(self._drink_id)
        if drink and drink.name:
            self._attr_name = drink.name
            self._attr_translation_key = None
            self._attr_translation_placeholders = {}
        else:
            # An unnamed custom slot, or a drink no longer listed: named by
            # translation, so the name attribute must be absent, not None.
            self.__dict__.pop("_attr_name", None)
            self._attr_translation_key = "custom_drink" if drink and drink.is_custom else "drink"
            self._attr_translation_placeholders = {"number": str(self._drink_id)}
        self._attr_icon = drink_icon(drink) if drink else "mdi:coffee"
        self._attr_entity_picture = self.coordinator.drinks.photo(self._drink_id)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def refresh() -> None:
            self._update_from_list()
            self.async_write_ha_state()

        self.async_on_remove(self.coordinator.drinks.add_listener(refresh))

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        drink = self.coordinator.drinks.get(self._drink_id)
        attributes: dict[str, object] = {"drink_id": self._drink_id}
        if drink:
            attributes["groups"] = sorted(g.lower() for g in drink.groups)
            if drink.description:
                attributes["description"] = drink.description
        return attributes

    @property
    def available(self) -> bool:
        """Only when a drink can safely start (see ``DaedalusCoordinator.can_brew``)."""
        return (
            super().available
            and self.coordinator.can_brew
            and self.coordinator.drinks.get(self._drink_id) is not None
        )

    async def async_press(self) -> None:
        """Start the drink, re-checking at the moment of pressing."""
        if not self.coordinator.can_brew:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cannot_brew_now"
            )
        await self.coordinator.async_brew(self._drink_id)
