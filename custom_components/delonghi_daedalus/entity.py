"""Base entity for De'Longhi Daedalus: one place for naming and the device."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import DaedalusCoordinator


class DaedalusEntity(CoordinatorEntity[DaedalusCoordinator]):
    """An entity of one coffee machine, named by its translation key."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: DaedalusCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.appliance.serial_number}_{key}"
        self._attr_device_info = coordinator.device_info
