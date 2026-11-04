"""Switch platform for De'Longhi Daedalus coffee machines."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
    """Set up the De'Longhi Daedalus switch platform."""
    coordinator: DaedalusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DaedalusPowerSwitch(coordinator)])


class DaedalusPowerSwitch(CoordinatorEntity[DaedalusCoordinator], SwitchEntity):
    """Controls coffee machine standby / wake state."""

    _attr_has_entity_name = True
    _attr_translation_key = "power"
    _attr_icon = "mdi:coffee-maker"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        """Initialize the power switch entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.appliance.serial_number}_power"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.appliance.serial_number)},
            name=coordinator.appliance.display_name,
            manufacturer=MANUFACTURER,
            model=coordinator.appliance.model,
            serial_number=coordinator.appliance.serial_number,
        )

    @property
    def is_on(self) -> bool:
        """Return True if machine is awake / powered on."""
        return self.coordinator.data.is_on

    @property
    def available(self) -> bool:
        """Return True if machine is reachable locally or via cloud."""
        return self.coordinator.data.is_local_connected or self.coordinator.data.is_cloud_online

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Wake machine from standby."""
        await self.coordinator.async_turn_on()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Put machine to sleep / standby."""
        await self.coordinator.async_turn_off()
