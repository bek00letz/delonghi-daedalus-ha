"""Switch platform for De'Longhi Daedalus coffee machines."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DaedalusCoordinator
from .entity import DaedalusEntity

# Switches in the machine's UserConf settings: (entity key, field name).
MACHINE_SWITCHES: tuple[tuple[str, str], ...] = (
    ("sound", "SoundEnable"),
    ("energy_saving", "EnergySavingMode"),
    ("cup_warmer", "CupWarmer"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the De'Longhi Daedalus switch platform."""
    coordinator: DaedalusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DaedalusPowerSwitch(coordinator),
            *(DaedalusMachineSwitch(coordinator, key, name) for key, name in MACHINE_SWITCHES),
        ]
    )


class DaedalusPowerSwitch(DaedalusEntity, SwitchEntity):
    """Controls coffee machine standby / wake state."""

    _attr_icon = "mdi:coffee-maker"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        """Initialize the power switch entity."""
        super().__init__(coordinator, "power")

    @property
    def is_on(self) -> bool | None:
        """Return whether the machine is awake; unknown until it has reported."""
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


class DaedalusMachineSwitch(DaedalusEntity, SwitchEntity):
    """One of the machine's on/off settings."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: DaedalusCoordinator, key: str, field_name: str) -> None:
        super().__init__(coordinator, key)
        self._field_name = field_name

    @property
    def is_on(self) -> bool | None:
        settings = self.coordinator.data.settings
        return None if settings is None else settings.switches.get(self._field_name)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_switch(self._field_name, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_switch(self._field_name, False)
