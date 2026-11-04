"""Binary sensor platform for De'Longhi Daedalus coffee machines."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DaedalusCoordinator


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
        ]
    )


class DaedalusBaseBinarySensor(CoordinatorEntity[DaedalusCoordinator], BinarySensorEntity):
    """Base class for Daedalus binary sensors."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: DaedalusCoordinator, key: str) -> None:
        """Initialize the binary sensor."""
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
