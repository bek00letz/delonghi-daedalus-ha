"""Data models for De'Longhi Daedalus appliances and wire protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any

from ..const import (
    RECIPE_NAMES,
    STATE_OFFLINE,
    STATE_READY,
    STATE_STANDBY,
)


@dataclass
class DaedalusAppliance:
    """Represents a discovered De'Longhi Daedalus appliance."""

    machine_name: str
    serial_number: str
    model: str
    sku: str
    status: str
    local_ip: str | None = None
    auth_token: str | None = None

    @property
    def display_name(self) -> str:
        """Return a human-friendly display name."""
        if "470" in self.model:
            return "De'Longhi Eletta Ultra"
        return f"De'Longhi {self.model}"


@dataclass
class BeverageParameters:
    """Parameters passed to BeverageRequest."""

    recipe_id: int
    profile_id: int = 1
    water: int | None = None
    aroma: int | None = None
    milk: int | None = None
    temperature: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert non-null parameters to wire dict format."""
        params: dict[str, Any] = {}
        if self.water is not None:
            params["Water"] = self.water
        if self.aroma is not None:
            params["Aroma"] = self.aroma
        if self.milk is not None:
            params["Milk"] = self.milk
        if self.temperature is not None:
            params["Temperature"] = self.temperature
        return params


@dataclass
class BeverageReport:
    """Telemetry report emitted after dispensing a beverage."""

    recipe_id: int
    profile_id: int
    water: int | None = None
    aroma: int | None = None
    powder_qty: int | None = None
    mug_index: int | None = None
    pre_infusion_index: int | None = None
    infusion_index: int | None = None
    rinse: int | None = None
    timestamp: float = field(default_factory=time.time)

    @property
    def recipe_name(self) -> str:
        """Return friendly drink name."""
        return RECIPE_NAMES.get(self.recipe_id, f"Recipe {self.recipe_id}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BeverageReport:
        """Parse BeverageRequestReport payload."""
        return cls(
            recipe_id=int(data.get("RecipeId", 0)),
            profile_id=int(data.get("ProfileId", 1)),
            water=data.get("Water"),
            aroma=data.get("Aroma"),
            powder_qty=data.get("IdPowderQty"),
            mug_index=data.get("IdMugIndex"),
            pre_infusion_index=data.get("PreInfusionIndex"),
            infusion_index=data.get("InfusionIndex"),
            rinse=data.get("Rinse"),
            timestamp=time.time(),
        )


@dataclass
class MachineSettings:
    """Configurable machine settings."""

    water_temperature: int | None = None
    auto_off: int | None = None
    water_hardness: int | None = None
    energy_saver: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert non-null settings to wire dict format."""
        payload: dict[str, Any] = {}
        if self.water_temperature is not None:
            payload["WaterTemperature"] = self.water_temperature
        if self.auto_off is not None:
            payload["AutoOff"] = self.auto_off
        if self.water_hardness is not None:
            payload["WaterHardness"] = self.water_hardness
        if self.energy_saver is not None:
            payload["EnergySaver"] = 1 if self.energy_saver else 0
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MachineSettings:
        """Parse machine settings payload."""
        return cls(
            water_temperature=data.get("WaterTemperature") or data.get("waterTemperature"),
            auto_off=data.get("AutoOff") or data.get("autoOff"),
            water_hardness=data.get("WaterHardness") or data.get("waterHardness"),
            energy_saver=bool(data.get("EnergySaver") or data.get("energySaver", 0)),
        )


@dataclass
class MachineState:
    """Comprehensive snapshot of coffee machine state."""

    is_on: bool = False
    operational_state: str = STATE_OFFLINE
    is_local_connected: bool = False
    is_cloud_online: bool = False
    last_drink: BeverageReport | None = None
    settings: MachineSettings = field(default_factory=MachineSettings)

    def update_from_ui_response(self, turn_on_off: int) -> None:
        """Update power state from MachineUI / MachineUIResponse."""
        if turn_on_off == 2:
            self.is_on = True
            self.operational_state = STATE_READY
        elif turn_on_off == 1:
            self.is_on = False
            self.operational_state = STATE_STANDBY
