"""Button platform for De'Longhi Daedalus quick drink brewing triggers."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MANUFACTURER,
    RECIPE_AMERICANO,
    RECIPE_COFFEE,
    RECIPE_DOPPIO_PLUS,
    RECIPE_ESPRESSO,
    RECIPE_HOT_WATER,
    RECIPE_LONG,
    RECIPE_NAMES,
    RECIPE_SIMPLE_COFFEE,
)
from .coordinator import DaedalusCoordinator

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


class DaedalusBrewButton(CoordinatorEntity[DaedalusCoordinator], ButtonEntity):
    """Button entity that triggers a specific coffee or hot water recipe."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: DaedalusCoordinator,
        recipe_id: int,
        key: str,
        icon: str,
    ) -> None:
        """Initialize brew button."""
        super().__init__(coordinator)
        self._recipe_id = recipe_id
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_unique_id = f"{coordinator.appliance.serial_number}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.appliance.serial_number)},
            name=coordinator.appliance.display_name,
            manufacturer=MANUFACTURER,
            model=coordinator.appliance.model,
            serial_number=coordinator.appliance.serial_number,
        )

    @property
    def available(self) -> bool:
        """Button is available when machine is powered on and locally connected."""
        return self.coordinator.data.is_local_connected and self.coordinator.data.is_on

    async def async_press(self) -> None:
        """Trigger brewing the assigned recipe."""
        await self.coordinator.async_brew(self._recipe_id)

