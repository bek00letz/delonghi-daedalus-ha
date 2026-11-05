"""Data models for De'Longhi Daedalus appliances and wire protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import time
from typing import Any

from ..const import (
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BeverageReport:
        """Parse BeverageRequestReport payload."""
        return cls(
            recipe_id=_int(data.get("RecipeId")) or 0,
            profile_id=_int(data.get("ProfileId")) or 1,
            # Coffee reports "Water"; hot water reports "WaterQty2" (seen 2026-11-04).
            water=data.get("Water", data.get("WaterQty2")),
            aroma=data.get("Aroma"),
            powder_qty=data.get("IdPowderQty"),
            mug_index=data.get("IdMugIndex"),
            pre_infusion_index=data.get("PreInfusionIndex"),
            infusion_index=data.get("InfusionIndex"),
            rinse=data.get("Rinse"),
            timestamp=time.time(),
        )


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass
class MachineSettings:
    """Settings and counters from the machine's MachineSettings record in the cloud.

    Seen on an ECAM 470.85 (2026-11-04): {"Editable": {"CoffeeTemp", "AutoTurnOffTimeInMin",
    "WaterHardness", "SetProfile", "UserConf": {...switches}}, "NotEditable": {...counters}}.
    """

    coffee_temperature: int | None = None  # level index into the machine's CoffeeTemperatures
    auto_off_minutes: int | None = None
    water_hardness: int | None = None  # level index into the machine's WaterHardness
    active_profile: int | None = None
    switches: dict[str, bool] = field(default_factory=dict)  # UserConf, e.g. SoundEnable
    descale_progress: int | None = None  # percent of the way to the next descaling
    filter_usage: int | None = None  # percent of the water filter's life used
    filters_installed: int | None = None  # lifetime count; goes up when a new filter is registered
    grounds_container: int | None = None  # percent full
    coffees_brewed: int | None = None  # coffee grounds count
    water_total_ml: int | None = None
    milk_cleanings: int | None = None
    grind_setting: int | None = None

    @classmethod
    def from_report(cls, body: Any) -> MachineSettings | None:
        """Parse the record's ``reported`` state; None if it isn't one."""
        if not isinstance(body, dict):
            return None
        edit = body.get("Editable") if isinstance(body.get("Editable"), dict) else {}
        fixed = body.get("NotEditable") if isinstance(body.get("NotEditable"), dict) else {}
        if not edit and not fixed:
            return None
        conf = edit.get("UserConf") if isinstance(edit.get("UserConf"), dict) else {}
        return cls(
            coffee_temperature=_int(edit.get("CoffeeTemp")),
            auto_off_minutes=_int(edit.get("AutoTurnOffTimeInMin")),
            water_hardness=_int(edit.get("WaterHardness")),
            active_profile=_int(edit.get("SetProfile")),
            switches={k: v for k, v in conf.items() if isinstance(v, bool)},
            descale_progress=_int(fixed.get("DecalPerc")),
            filter_usage=_int(fixed.get("FilterUsagePerc")),
            filters_installed=_int(fixed.get("FilterTotCnt")),
            grounds_container=_int(fixed.get("GroundCntPerc")),
            coffees_brewed=_int(fixed.get("CoffeeGroundsCnt")),
            water_total_ml=_int(fixed.get("WaterTotQty")),
            milk_cleanings=_int(fixed.get("MilkCleanCnt")),
            grind_setting=_int(fixed.get("Granulometry")),
        )


@dataclass
class MachineCapabilities:
    """What this machine is and which options it offers (MachineCapabilities record)."""

    sku: str | None = None
    mac: str | None = None
    firmware_ui: str | None = None
    firmware_wifi: str | None = None
    firmware_mainboard: str | None = None
    coffee_temperatures: list[int] = field(default_factory=list)
    auto_off_minutes: list[int] = field(default_factory=list)
    water_hardness_levels: list[int] = field(default_factory=list)
    profiles: int | None = None

    @classmethod
    def from_report(cls, body: Any) -> MachineCapabilities | None:
        if not isinstance(body, dict) or not body:
            return None

        def text(key: str) -> str | None:
            value = body.get(key)
            return value if isinstance(value, str) and value else None

        def ints(key: str) -> list[int]:
            value = body.get(key)
            return [v for v in value if _int(v) is not None] if isinstance(value, list) else []

        return cls(
            sku=text("SKU"),
            mac=text("MAC"),
            firmware_ui=text("FWUIVersion"),
            firmware_wifi=text("FWWiFiVersion"),
            firmware_mainboard=text("FWMainboardVersion"),
            coffee_temperatures=ints("CoffeeTemperatures"),
            auto_off_minutes=ints("AutoTurnOffTimeInMinutes"),
            water_hardness_levels=ints("WaterHardness"),
            profiles=_int(body.get("MachineUserProfiles")),
        )


# Status words the machine has been seen to send in StatusService reports,
# mapped to plain words. Anything else is shown as the machine sends it.
STATUS_WORDS: dict[str, str] = {
    "Standby": "Standby",
    "Idle": "Ready",
    "Busy": "Busy",  # brewing (step e.g. "Water Heating Up"); possibly also rinsing
}
STATUS_BUSY = "Busy"
STATUS_STANDBY = "Standby"


@dataclass
class MachineStatus:
    """One StatusService report, as the machine sends it over the local connection.

    Seen on an ECAM 470.85: {"Message": "StatusService", "Response": "<JSON text>"}
    where the JSON holds Status, CurrMode, alarms, peripherals and accessories.
    """

    status: str
    mode: int | None = None
    function_progress: int | None = None
    beverage_dispensing: int | None = None
    step: str | None = None
    beverage_error: bool = False
    alarms: dict[str, bool] = field(default_factory=dict)
    peripherals: dict[str, bool] = field(default_factory=dict)
    accessories: dict[str, bool] = field(default_factory=dict)
    # The machine's own idea of its address; only in the cloud's stored copy.
    reported_ip: str | None = None

    @property
    def is_on(self) -> bool:
        """True unless the machine reports standby."""
        return self.status != STATUS_STANDBY

    @property
    def brew_progress(self) -> int:
        """How far the current drink is, 0-100; 0 when nothing is being made."""
        if self.status != STATUS_BUSY or not isinstance(self.beverage_dispensing, int):
            return 0
        return max(0, min(100, self.beverage_dispensing))

    @property
    def display_status(self) -> str:
        """The status in plain words."""
        return STATUS_WORDS.get(self.status, self.status)

    @classmethod
    def from_message(cls, data: dict[str, Any]) -> MachineStatus | None:
        """Parse a StatusService message. None if it isn't one or can't be read."""
        if data.get("Message") != "StatusService":
            return None
        body = data.get("Response")
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except ValueError:
                return None
        return cls.from_report(body)

    @classmethod
    def from_report(cls, body: Any) -> MachineStatus | None:
        """Parse a status report body: a local StatusService or the cloud's stored copy."""
        if not isinstance(body, dict) or not isinstance(body.get("Status"), str):
            return None

        def flags(key: str) -> dict[str, bool]:
            value = body.get(key)
            return {k: bool(v) for k, v in value.items()} if isinstance(value, dict) else {}

        return cls(
            status=body["Status"],
            mode=body.get("CurrMode"),
            function_progress=body.get("CurrFuncProgress"),
            beverage_dispensing=body.get("BeverageDispensing"),
            step=body.get("StepDescr"),
            beverage_error=bool(body.get("ExitBeverageWithError", False)),
            alarms=flags("alarms"),
            peripherals=flags("peripherals"),
            accessories=flags("accessories"),
            reported_ip=body.get("LanIpAddress") if isinstance(body.get("LanIpAddress"), str) else None,
        )


@dataclass
class MachineState:
    """Comprehensive snapshot of coffee machine state."""

    # None means "not known yet": nothing has reported it since startup.
    is_on: bool | None = None
    operational_state: str | None = None
    is_local_connected: bool = False
    is_cloud_online: bool = False
    last_drink: BeverageReport | None = None
    settings: MachineSettings | None = None
    capabilities: MachineCapabilities | None = None
    is_cloud_connected: bool = False
    status: MachineStatus | None = None

    def update_from_status(self, status: MachineStatus) -> None:
        """Take power state from the machine's own status report."""
        self.status = status
        self.is_on = status.is_on
        self.operational_state = STATE_READY if status.is_on else STATE_STANDBY

    def update_from_ui_response(self, turn_on_off: int) -> None:
        """Update power state from MachineUI / MachineUIResponse."""
        if turn_on_off == 2:
            self.is_on = True
            self.operational_state = STATE_READY
        elif turn_on_off == 1:
            self.is_on = False
            self.operational_state = STATE_STANDBY
