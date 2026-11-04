"""Settings with a fixed set of choices, offered as the machine reports them."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .api.models import MachineCapabilities, MachineSettings
from .const import DOMAIN
from .coordinator import DaedalusCoordinator
from .entity import DaedalusEntity

# Names for coffee temperature levels by index; the ECAM 470.85 offers three.
TEMPERATURE_NAMES = ["low", "medium", "high", "maximum"]


@dataclass(frozen=True, kw_only=True)
class SettingSelectDescription(SelectEntityDescription):
    """A setting whose choices come from the machine's capabilities."""

    field_name: str  # name in setMachineSettings and the MachineSettings record
    choices: Callable[[MachineCapabilities | None], list[int]]  # the machine's raw values
    label: Callable[[int], str | None]  # option shown for a raw value
    current: Callable[[MachineSettings], int | None]


def _from_caps(attr: str, fallback: list[int]) -> Callable[[MachineCapabilities | None], list[int]]:
    """The machine's own list of choices, or a safe default until it's known."""
    return lambda caps: (getattr(caps, attr) if caps else None) or fallback


SELECTS: tuple[SettingSelectDescription, ...] = (
    SettingSelectDescription(
        key="coffee_temperature",
        field_name="CoffeeTemp",
        choices=_from_caps("coffee_temperatures", [0, 1, 2]),
        label=lambda i: TEMPERATURE_NAMES[i] if 0 <= i < len(TEMPERATURE_NAMES) else None,
        current=lambda s: s.coffee_temperature,
    ),
    SettingSelectDescription(
        key="auto_off_timer",
        field_name="AutoTurnOffTimeInMin",
        choices=_from_caps("auto_off_minutes", [15, 30, 60, 180]),
        label=str,
        current=lambda s: s.auto_off_minutes,
    ),
    SettingSelectDescription(
        key="water_hardness",
        field_name="WaterHardness",
        entity_category=EntityCategory.CONFIG,
        choices=_from_caps("water_hardness_levels", [0, 1, 2, 3]),
        label=lambda i: f"level_{i + 1}",
        current=lambda s: s.water_hardness,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DaedalusCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            DaedalusBrewProfileSelect(coordinator),
            *(DaedalusSettingSelect(coordinator, d) for d in SELECTS),
        ]
    )


class DaedalusBrewProfileSelect(DaedalusEntity, SelectEntity, RestoreEntity):
    """Which profile Home Assistant brews with; remembered across restarts."""

    _attr_icon = "mdi:account-circle"

    def __init__(self, coordinator: DaedalusCoordinator) -> None:
        super().__init__(coordinator, "brew_profile")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) and last.state.isdigit():
            self.coordinator.brew_profile = int(last.state)

    @property
    def options(self) -> list[str]:
        """Only profiles seen in use: the machine supports more, but not all may exist."""
        known = set(self.coordinator.known_profiles)
        if (current := self.current_option) is not None:
            known.add(int(current))
        return [str(i) for i in sorted(known)] or ["1"]

    @property
    def current_option(self) -> str | None:
        profile = self.coordinator.brew_profile or self.coordinator.machine_profile
        return str(profile) if profile else None

    async def async_select_option(self, option: str) -> None:
        self.coordinator.brew_profile = int(option)
        self.async_write_ha_state()


class DaedalusSettingSelect(DaedalusEntity, SelectEntity):
    """One of the machine's settings with fixed choices."""

    entity_description: SettingSelectDescription

    def __init__(self, coordinator: DaedalusCoordinator, description: SettingSelectDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Only once the current settings are known."""
        return super().available and self.coordinator.data.settings is not None

    def _raw_choices(self) -> dict[str, int]:
        d = self.entity_description
        out: dict[str, int] = {}
        for raw in d.choices(self.coordinator.data.capabilities):
            if (label := d.label(raw)) is not None:
                out[label] = raw
        return out

    @property
    def options(self) -> list[str]:
        return list(self._raw_choices())

    @property
    def current_option(self) -> str | None:
        settings = self.coordinator.data.settings
        if settings is None or (raw := self.entity_description.current(settings)) is None:
            return None
        return self.entity_description.label(raw)

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_setting(
            self.entity_description.field_name, self._raw_choices()[option]
        )
