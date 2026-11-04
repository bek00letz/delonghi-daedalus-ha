"""Binary sensor platform for De'Longhi Daedalus coffee machines."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DaedalusCoordinator
from .entity import DaedalusEntity

# Alarm flags from the machine's StatusService report: (flag, translation key, on by default).
# Everyday ones are on by default; technical faults are created but switched off.
ALARMS: tuple[tuple[str, str, bool], ...] = (
    ("TankLevel", "alarm_water_tank_empty", True),
    ("TankPresence", "alarm_water_tank_missing", True),
    ("Waste", "alarm_grounds_container_full", True),
    ("NoCoffee", "alarm_beans_empty", True),
    ("Descale", "alarm_descale", True),
    ("Filter", "alarm_water_filter", True),
    ("BrewUnitClean", "alarm_brew_unit_clean", True),
    ("Clean", "alarm_clean", True),
    ("General", "alarm_general", True),
    ("GroundFine", "alarm_grind_too_fine", False),
    ("BrokenGrinder", "alarm_grinder", False),
    ("Infuser", "alarm_infuser", False),
    ("Imbocco", "alarm_imbocco", False),
    ("MotorPos", "alarm_motor_position", False),
    ("HeaterNTC", "alarm_heater_sensor", False),
    ("Bubble", "alarm_air_in_circuit", False),
    ("Drain", "alarm_drain", False),
    ("Fan", "alarm_fan", False),
    ("Fridge", "alarm_milk_fridge", False),
    ("UIMemory", "alarm_display_memory", False),
    ("WifiDown", "alarm_wifi", False),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up De'Longhi Daedalus binary sensors from config entry."""
    coordinator: DaedalusCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            DaedalusLocalConnectivityBinarySensor(coordinator),
            DaedalusCloudConnectivityBinarySensor(coordinator),
            *(
                DaedalusAlarmBinarySensor(coordinator, flag, key, enabled)
                for flag, key, enabled in ALARMS
            ),
        ]
    )


class DaedalusBaseBinarySensor(DaedalusEntity, BinarySensorEntity):
    """Base class for Daedalus binary sensors."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY


class DaedalusLocalConnectivityBinarySensor(DaedalusBaseBinarySensor):
    """Reports whether low-latency local LAN WebSocket connection is active."""

    _attr_icon = "mdi:wifi-check"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        """Initialize local connectivity sensor."""
        super().__init__(coordinator, "local_network")

    @property
    def is_on(self) -> bool:
        """Return True if local LAN WebSocket is connected and authenticated."""
        return self.coordinator.data.is_local_connected


class DaedalusCloudConnectivityBinarySensor(DaedalusBaseBinarySensor):
    """Reports whether coffee machine is reporting online to cloud services."""

    _attr_icon = "mdi:cloud-check"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        """Initialize cloud connectivity sensor."""
        super().__init__(coordinator, "cloud_status")

    @property
    def is_on(self) -> bool:
        """Return True if cloud status is reported online."""
        return self.coordinator.data.is_cloud_online



class DaedalusAlarmBinarySensor(DaedalusBaseBinarySensor):
    """One alarm flag from the machine's status report. On means attention needed."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self, coordinator: DaedalusCoordinator, flag: str, key: str, enabled: bool
    ) -> None:
        """Initialize the alarm sensor."""
        super().__init__(coordinator, key)
        self._flag = flag
        self._attr_entity_registry_enabled_default = enabled
        if not enabled:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def is_on(self) -> bool | None:
        """Return the alarm flag; unknown until the machine has sent a report."""
        status = self.coordinator.data.status
        if status is None or self._flag not in status.alarms:
            return None
        return status.alarms[self._flag]
