"""Button platform for De'Longhi Daedalus quick drink brewing triggers."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    RECIPE_AMERICANO,
    RECIPE_COFFEE,
    RECIPE_DOPPIO_PLUS,
    RECIPE_ESPRESSO,
    RECIPE_HOT_WATER,
    RECIPE_LONG,
    RECIPE_SIMPLE_COFFEE,
)
from .coordinator import DaedalusCoordinator
from .entity import DaedalusEntity

SUPPORTED_BUTTON_RECIPES = [
    (RECIPE_SIMPLE_COFFEE, "brew_coffee_xl", "mdi:coffee"),
    (RECIPE_ESPRESSO, "brew_espresso", "mdi:coffee-outline"),
    (RECIPE_COFFEE, "brew_coffee", "mdi:coffee"),
    (RECIPE_LONG, "brew_long", "mdi:coffee-outline"),
    (RECIPE_DOPPIO_PLUS, "brew_doppio_plus", "mdi:coffee"),
    (RECIPE_AMERICANO, "brew_americano", "mdi:coffee"),
    (RECIPE_HOT_WATER, "brew_hot_water", "mdi:water-boiler"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up De'Longhi Daedalus brew trigger buttons from config entry."""
    coordinator: DaedalusCoordinator = hass.data[DOMAIN][entry.entry_id]

    buttons = [
        DaedalusBrewButton(coordinator, recipe_id, key, icon)
        for recipe_id, key, icon in SUPPORTED_BUTTON_RECIPES
    ]
    async_add_entities(buttons)


class DaedalusBrewButton(DaedalusEntity, ButtonEntity):
    """Button entity that triggers a specific coffee or hot water recipe."""

    def __init__(
        self,
        coordinator: DaedalusCoordinator,
        recipe_id: int,
        key: str,
        icon: str,
    ) -> None:
        """Initialize brew button."""
        super().__init__(coordinator, key)
        self._recipe_id = recipe_id
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        """Only when a drink can safely start (see ``DaedalusCoordinator.can_brew``)."""
        return super().available and self.coordinator.can_brew

    async def async_press(self) -> None:
        """Start the drink, re-checking at the moment of pressing."""
        if not self.coordinator.can_brew:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cannot_brew_now"
            )
        await self.coordinator.async_brew(self._recipe_id)

