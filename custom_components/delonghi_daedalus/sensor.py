"""Sensors for De'Longhi Daedalus coffee machines."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api.models import MachineSettings, MachineState
from .const import DOMAIN
from .coordinator import DaedalusCoordinator
from .entity import DaedalusEntity

@dataclass(frozen=True, kw_only=True)
class SettingsSensorDescription(SensorEntityDescription):
    """A sensor fed by the machine's MachineSettings record."""

    value: Callable[[MachineSettings], Any]


SETTINGS_SENSORS: tuple[SettingsSensorDescription, ...] = (
    SettingsSensorDescription(
        key="active_profile",
        value=lambda s: s.active_profile,
    ),
    SettingsSensorDescription(
        key="descale_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.descale_progress,
    ),
    SettingsSensorDescription(
        key="water_filter_used",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.filter_usage,
    ),
    SettingsSensorDescription(
        key="grounds_container",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value=lambda s: s.grounds_container,
    ),
    SettingsSensorDescription(
        key="coffees_brewed",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: s.coffees_brewed,
    ),
    SettingsSensorDescription(
        key="water_used",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.LITERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: None if s.water_total_ml is None else s.water_total_ml / 1000,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up De'Longhi Daedalus sensors from a config entry."""
    coordinator: DaedalusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DaedalusStatusSensor(coordinator),
            DaedalusLastDrinkSensor(coordinator),
            DaedalusBrewProgressSensor(coordinator),
            DaedalusFilterChangedSensor(coordinator),
            *(DaedalusSettingsSensor(coordinator, d) for d in SETTINGS_SENSORS),
        ]
    )


class DaedalusStatusSensor(DaedalusEntity, SensorEntity):
    """The machine's own status word."""

    _attr_icon = "mdi:coffee-maker-check"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str | None:
        """The machine's own status; unknown until something has reported it."""
        state: MachineState = self.coordinator.data
        if state.status is not None:
            return state.status.display_status
        return state.operational_state.capitalize() if state.operational_state else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The raw report fields whose meaning isn't confirmed yet."""
        status = self.coordinator.data.status
        if status is None:
            return {}
        return {
            "machine_status": status.status,
            "mode": status.mode,
            "step": status.step,
            "function_progress": status.function_progress,
            "beverage_dispensing": status.beverage_dispensing,
            "peripherals": status.peripherals,
            "accessories": status.accessories,
        }


class DaedalusFilterChangedSensor(DaedalusEntity, SensorEntity):
    """When a new water filter was last registered on the machine."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:water-check"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        super().__init__(coordinator, "water_filter_changed")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.filter_changed


class DaedalusBrewProgressSensor(DaedalusEntity, SensorEntity):
    """How far the drink being made is, from the machine's live reports."""

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:coffee-to-go"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        super().__init__(coordinator, "brew_progress")

    @property
    def native_value(self) -> int | None:
        status = self.coordinator.data.status
        return None if status is None else status.brew_progress


class DaedalusLastDrinkSensor(DaedalusEntity, SensorEntity):
    """The last drink brewed from Home Assistant, with its extraction details."""

    _attr_icon = "mdi:coffee"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        # Key kept from earlier versions so the entity keeps its ID.
        super().__init__(coordinator, "last_beverage")

    @property
    def native_value(self) -> str | None:
        drink = self.coordinator.data.last_drink
        return drink.recipe_name if drink else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        drink = self.coordinator.data.last_drink
        if not drink:
            return {}
        return {
            "recipe_id": drink.recipe_id,
            "profile_id": drink.profile_id,
            "water_ml": drink.water,
            "aroma_level": drink.aroma,
            "powder_quantity": drink.powder_qty,
            "mug_index": drink.mug_index,
            "pre_infusion_stage": drink.pre_infusion_index,
            "infusion_stage": drink.infusion_index,
            "rinse": bool(drink.rinse),
            "timestamp": drink.timestamp,
        }


class DaedalusSettingsSensor(DaedalusEntity, SensorEntity):
    """One value from the machine's settings and counters."""

    entity_description: SettingsSensorDescription

    def __init__(
        self, coordinator: DaedalusCoordinator, description: SettingsSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        settings = self.coordinator.data.settings
        return None if settings is None else self.entity_description.value(settings)
