"""Sensor platform for De'Longhi Daedalus coffee machines."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    AUTO_OFF_LABELS,
    DOMAIN,
    MANUFACTURER,
    WATER_TEMP_LABELS,
)
from .coordinator import DaedalusCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up De'Longhi Daedalus sensors from config entry."""
    coordinator: DaedalusCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            DaedalusStatusSensor(coordinator),
            DaedalusLastBeverageSensor(coordinator),
            DaedalusTemperatureSensor(coordinator),
            DaedalusAutoOffSensor(coordinator),
        ]
    )


class DaedalusBaseSensor(CoordinatorEntity[DaedalusCoordinator], SensorEntity):
    """Base class for Daedalus sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DaedalusCoordinator, key: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.appliance.serial_number}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.appliance.serial_number)},
            name=coordinator.appliance.display_name,
            manufacturer=MANUFACTURER,
            model=coordinator.appliance.model,
            serial_number=coordinator.appliance.serial_number,
        )


class DaedalusStatusSensor(DaedalusBaseSensor):
    """Sensor reporting active operational state of coffee machine."""

    _attr_icon = "mdi:coffee-maker-check"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        """Initialize status sensor."""
        super().__init__(coordinator, "status")

    @property
    def native_value(self) -> str:
        """Return the machine's own status when it has reported one."""
        state = self.coordinator.data
        if state.status is not None:
            return state.status.display_status
        return state.operational_state.capitalize()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the raw report fields whose meaning isn't confirmed yet."""
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


class DaedalusLastBeverageSensor(DaedalusBaseSensor):
    """Sensor tracking the last brewed beverage with full telemetry."""

    _attr_icon = "mdi:coffee"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        """Initialize last beverage sensor."""
        super().__init__(coordinator, "last_beverage")

    @property
    def native_value(self) -> str | None:
        """Return the friendly name of the last dispensed beverage."""
        if self.coordinator.data.last_drink:
            return self.coordinator.data.last_drink.recipe_name
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extended beverage extraction attributes."""
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


class DaedalusTemperatureSensor(DaedalusBaseSensor):
    """Sensor displaying configured coffee extraction temperature."""

    _attr_icon = "mdi:thermometer"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        """Initialize temperature sensor."""
        super().__init__(coordinator, "coffee_temperature")

    @property
    def native_value(self) -> str | None:
        """Return the temperature label."""
        temp = self.coordinator.data.settings.water_temperature
        if temp is not None:
            return WATER_TEMP_LABELS.get(temp, str(temp))
        return None


class DaedalusAutoOffSensor(DaedalusBaseSensor):
    """Sensor displaying configured auto-off standby timeout."""

    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        """Initialize auto-off sensor."""
        super().__init__(coordinator, "auto_off_timer")

    @property
    def native_value(self) -> str | None:
        """Return auto-off duration label."""
        auto_off = self.coordinator.data.settings.auto_off
        if auto_off is not None:
            return AUTO_OFF_LABELS.get(auto_off, f"{auto_off} minutes")
        return None

