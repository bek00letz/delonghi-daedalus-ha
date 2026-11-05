"""What Home Assistant asks the machine for when it makes a drink.

The machine only accepts a drink request that carries the drink's values,
top level, with the active profile (seen 2026-11-05: hot water with only its
amount was ignored; with its full set of values it poured 60 ml as asked, and
an espresso built from De'Longhi's values poured 40 ml at strength 5).

A drink's values come from three places, later ones winning:

1. De'Longhi's drink list: the factory values for the model and firmware.
2. How the machine last made that drink for that profile, from its report
   after every drink, so a drink keeps the person's own recipe (their
   strength 3, their 300 ml of tea water).
3. Changes saved in Home Assistant for that drink (the card's sheet).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .api.content import Drink

# Values that are about the app, not the drink: a default per time of day,
# a display-only millilitre figure, and app bookkeeping. Not sent.
_APP_ONLY_SUFFIXES = ("Morning", "Afternoon", "Evening", "MlDef")
_APP_ONLY = frozenset({"BevProgMyFunct", "Accessory"})
# Report fields that aren't drink values.
_NOT_VALUES = frozenset(
    {"Message", "Response", "AppId", "RequestId", "RecipeId", "ProfileId", "OperationId",
     "ConnectionId", "ErrorDescription", "SerialNo", "Timestamp"}
)

# The values a person can change, in the order the sheet shows them:
# name → (label, kind). Kinds: "ml", "milk" (machine units shown as about
# so many ml), "scale" (steps), "count", "switch".
ADJUSTABLE: dict[str, tuple[str, str]] = {
    "Aroma": ("Strength", "scale"),
    "Water": ("Coffee", "ml"),
    "WaterQty2": ("Water", "ml"),
    "MilkQty": ("Milk", "milk"),
    # Not "MilkFoam": foam is set with the dial on the milk carafe's lid, not
    # by the machine; the value is still sent as the machine reports it.
    "TeaTemp": ("Temperature", "scale"),
    "NumIceCubes": ("Ice cubes", "count"),
    "IdMugAdjust": ("Mug size", "scale"),
    "Intensity": ("Intensity", "scale"),
    "ExtraShotEnabled": ("Extra shot", "switch"),
    "Double": ("Double", "switch"),
}


def sendable(name: str) -> bool:
    return name not in _APP_ONLY and not name.endswith(_APP_ONLY_SUFFIXES)


def machine_values(report: Mapping[str, Any]) -> dict[str, int]:
    """The drink values in a machine's report (whole numbers only)."""
    values: dict[str, int] = {}
    for name, value in report.items():
        if name in _NOT_VALUES or not sendable(name) or isinstance(value, bool):
            continue
        if isinstance(value, int):
            values[name] = value
        elif isinstance(value, str) and value.lstrip("-").isdigit():
            values[name] = int(value)
    return values


def _limits(drink: Drink | None, name: str) -> tuple[int, int, int] | None:
    return drink.values.get(name) if drink else None


def _clamp(drink: Drink | None, name: str, value: int) -> int:
    limits = _limits(drink, name)
    return value if limits is None else max(limits[0], min(limits[2], value))


def recipe(drink: Drink | None, reported: Mapping[str, int], personal: Mapping[str, int]) -> dict[str, int]:
    """The values to send for one drink and profile."""
    values: dict[str, int] = {}
    for name, (low, default, high) in (drink.values.items() if drink else ()):
        # Custom slots list 255 as "not set": a default outside its range isn't a value.
        if sendable(name) and low <= default <= high:
            values[name] = default
    listed = bool(drink and drink.values)
    for name, value in reported.items():
        # Reports also carry fields a request doesn't (IdPowderQty, …): with
        # De'Longhi's list known, take only values the list names.
        if sendable(name) and (not listed or name in drink.values):
            values[name] = value
    for name, value in personal.items():
        if name in ADJUSTABLE and (drink is None or name in drink.values):
            values[name] = _clamp(drink, name, value)
    return values


def controls(drink: Drink | None, reported: Mapping[str, int], personal: Mapping[str, int]) -> list[dict[str, Any]]:
    """What the sheet offers for one drink: only values that can actually change."""
    if drink is None:
        return []
    result = []
    for name, (label, kind) in ADJUSTABLE.items():
        limits = _limits(drink, name)
        if limits is None or limits[0] >= limits[2]:
            continue
        low, factory, high = limits
        usual = reported.get(name, factory if low <= factory <= high else low)
        usual = max(low, min(high, usual))
        control: dict[str, Any] = {
            "key": name,
            "label": label,
            "kind": kind,
            "min": low,
            "max": high,
            "step": 1 if kind in ("scale", "count", "switch") or high - low < 60 else 5,
            "usual": usual,
            "value": _clamp(drink, name, personal.get(name, usual)),
        }
        if kind == "milk":
            ml = _limits(drink, "MilkQtyMlDef")
            if ml and ml[1] > 0 and factory > 0:
                control["ml_per_unit"] = round(ml[1] / factory, 4)
            else:
                control["kind"] = "scale"
                control["step"] = max(1, round((high - low) / 20))
        result.append(control)
    return result


def clean_personal(drink: Drink | None, changes: Mapping[str, Any]) -> dict[str, int]:
    """Only values this drink can change, as whole numbers within its limits."""
    cleaned: dict[str, int] = {}
    for name, value in changes.items():
        limits = _limits(drink, name)
        if name not in ADJUSTABLE or limits is None or limits[0] >= limits[2]:
            continue
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, (int, float)):
            cleaned[name] = _clamp(drink, name, int(round(value)))
    return cleaned
