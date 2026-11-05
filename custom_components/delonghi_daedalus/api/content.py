"""De'Longhi's content server: the drinks a machine offers, with names and photos.

The My Coffee Lounge app reads the same two lists (seen 2026-11-05):

* ``GET /basicrecipe/retrieve`` — the drink list for one machine model, SKU
  and firmware, in one language: number, name, groups (hot, milk, mug…).
* ``GET /drinkimage`` — a photo set and a one-line description per drink
  number, for one machine model.

Both take the account token as ``Authorization: Bearer``. Nothing personal is
sent beyond what identifies the machine model and its firmware.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
from typing import Any

import aiohttp

from .client import DaedalusAuthError, DaedalusConnectionError

_LOGGER = logging.getLogger(__name__)

CONTENT_URL = "https://prd-eu-bff-daedalus.delonghigroup.com/dlg-prod"
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
# The photo shape the app uses for its drink grid (412×452).
PHOTO_KEY = "image_1x1"
FALLBACK_LOCALE = ("GB", "en-gb")


class DaedalusContentError(Exception):
    """The content server didn't give a usable answer."""


@dataclass(frozen=True)
class Drink:
    """One drink a machine can make."""

    id: int
    name: str | None  # None for an unnamed custom slot
    groups: frozenset[str] = field(default_factory=frozenset)
    photo_url: str | None = None
    description: str | None = None
    # The drink's values as De'Longhi lists them: {name: (lowest, default, highest)}.
    values: dict[str, tuple[int, int, int]] = field(default_factory=dict, compare=False)

    @property
    def is_custom(self) -> bool:
        return "CUSTOM" in self.groups

    @property
    def is_mug(self) -> bool:
        return "MUG" in self.groups

    @property
    def is_main(self) -> bool:
        """A drink of the main list: not a mug version or a custom slot."""
        return not (self.is_custom or self.is_mug)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "groups": sorted(self.groups),
            "photo_url": self.photo_url,
            "description": self.description,
            "values": {name: list(limits) for name, limits in self.values.items()},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Drink | None:
        try:
            return cls(
                id=int(data["id"]),
                name=data.get("name") if isinstance(data.get("name"), str) else None,
                groups=_groups(data.get("groups", [])),  # also repairs lists saved by 0.8.0
                photo_url=data.get("photo_url") if isinstance(data.get("photo_url"), str) else None,
                description=data.get("description") if isinstance(data.get("description"), str) else None,
                values=_values(data.get("values")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def locale_candidates(language: str | None, country: str | None) -> list[tuple[str, str]]:
    """(country, locale) pairs to try, best first, ending with British English."""
    lang = (language or "").replace("_", "-").lower()
    region = (country or "").upper()
    pairs: list[tuple[str, str]] = []
    if lang and region:
        locale = lang if "-" in lang else f"{lang}-{region.lower()}"
        pairs.append((region, locale))
    if lang and "-" in lang:
        pairs.append((lang.split("-")[1].upper(), lang))
    if region:
        pairs.append((region, FALLBACK_LOCALE[1]))
    pairs.append(FALLBACK_LOCALE)
    return list(dict.fromkeys(pairs))


def _readable(original: str) -> str:
    """'MilchKaffee' → 'Milch Kaffee', for drinks without a display name."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", original).strip()


def _groups(value: Any) -> frozenset[str]:
    """Groups arrive as ["HOT", "COFFEE"], and mug drinks as ["HOME", "MUG HOT", …]:
    two groups in one string, so split on commas and spaces."""
    if not isinstance(value, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        part.upper() for item in value if isinstance(item, str) for part in re.split(r"[\s,]+", item) if part
    )


def _values(raw: Any) -> dict[str, tuple[int, int, int]]:
    """{name: (lowest, default, highest)} from a saved list; anything odd is dropped."""
    if not isinstance(raw, dict):
        return {}
    values: dict[str, tuple[int, int, int]] = {}
    for name, limits in raw.items():
        try:
            low, default, high = (int(x) for x in limits)
        except (TypeError, ValueError):
            continue
        if isinstance(name, str):
            values[name] = (low, default, high)
    return values


def _ingredients(raw: Any) -> dict[str, tuple[int, int, int]]:
    """A drink's ingredients as De'Longhi lists them: [{name, minval, defval, maxval}, …]."""
    values: dict[str, tuple[int, int, int]] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        try:
            values[item["name"]] = (int(item.get("minval")), int(item.get("defval")), int(item.get("maxval")))
        except (TypeError, ValueError):
            continue
    return values


def parse_drinks(data: Any) -> list[Drink]:
    """The drink list from a ``basicrecipe/retrieve`` answer; odd entries are skipped."""
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries or not isinstance(entries[0], dict):
        return []
    drinks: dict[int, Drink] = {}
    for item in entries[0].get("drinks") or []:
        if not isinstance(item, dict) or item.get("display") is False:
            continue
        details = item.get("drink")
        drink = details[0] if isinstance(details, list) and details and isinstance(details[0], dict) else {}
        try:
            drink_id = int(drink.get("drink_id"))
        except (TypeError, ValueError):
            continue
        groups = _groups(drink.get("cluster"))
        name = drink.get("customized_title")
        if not isinstance(name, str) or not name.strip():
            original = drink.get("original_title") or item.get("title")
            name = _readable(original) if isinstance(original, str) and "CUSTOM" not in groups else None
        drinks.setdefault(
            drink_id,
            Drink(drink_id, name.strip() if name else None, groups, values=_ingredients(drink.get("ingredients"))),
        )
    return sorted(drinks.values(), key=lambda d: d.id)


def parse_photos(data: Any) -> dict[int, tuple[str | None, str | None]]:
    """{drink number: (photo link, description)} from a ``drinkimage`` answer."""
    if not isinstance(data, dict):
        return {}
    result: dict[int, list[str | None]] = {}

    def rows(key: str):
        for row in data.get(key) or []:
            if isinstance(row, dict):
                try:
                    yield int(row.get("drink_id")), row
                except (TypeError, ValueError):
                    continue

    for drink_id, row in rows("drinks_images"):
        url = row.get(PHOTO_KEY)
        if isinstance(url, str) and url.startswith("https://"):
            result.setdefault(drink_id, [None, None])[0] = url
    for drink_id, row in rows("drinks_description"):
        text = row.get("drink_description")
        if isinstance(text, str) and text.strip():
            result.setdefault(drink_id, [None, None])[1] = text.strip()
    return {k: (v[0], v[1]) for k, v in result.items()}


@dataclass(frozen=True)
class MachineIdentity:
    """What the content server needs to know about the machine."""

    model: str
    sku: str
    firmware_ui: str
    firmware_wifi: str
    firmware_mainboard: str

    @property
    def complete(self) -> bool:
        return all((self.model, self.sku, self.firmware_ui, self.firmware_wifi, self.firmware_mainboard))


async def _get(session: aiohttp.ClientSession, path: str, params: dict[str, str], token: str) -> Any:
    try:
        async with session.get(
            f"{CONTENT_URL}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        ) as resp:
            if resp.status in (401, 403):
                raise DaedalusAuthError(f"Content server refused the token (HTTP {resp.status})")
            if resp.status != 200:
                raise DaedalusContentError(f"{path}: HTTP {resp.status}")
            try:
                return await resp.json(content_type=None)
            except ValueError as err:
                raise DaedalusContentError(f"{path}: not JSON") from err
    except (aiohttp.ClientError, TimeoutError) as err:
        raise DaedalusConnectionError(f"Content server unreachable: {err}") from err


async def async_fetch_drinks(
    session: aiohttp.ClientSession,
    token: str,
    machine: MachineIdentity,
    language: str | None,
    country: str | None,
) -> list[Drink]:
    """The machine's drinks with names and photo links, in the best language on offer."""
    drinks: list[Drink] = []
    used_locale = FALLBACK_LOCALE[1]
    for region, locale in locale_candidates(language, country):
        try:
            data = await _get(
                session,
                "basicrecipe/retrieve",
                {
                    "machine_model": machine.model,
                    "sku": machine.sku,
                    "mb_firmware_version": machine.firmware_mainboard,
                    "wifi_firmware_version": machine.firmware_wifi,
                    "ui_firmware_version": machine.firmware_ui,
                    "country": region,
                    "locale": locale,
                },
                token,
            )
        except DaedalusContentError as err:
            _LOGGER.debug("No drink list for %s: %s", locale, err)
            continue
        if drinks := parse_drinks(data):
            used_locale = locale
            break
    if not drinks:
        raise DaedalusContentError("No drink list for this machine")

    try:
        photos = parse_photos(
            await _get(
                session,
                "drinkimage",
                {"machine_type": machine.model, "ui_firmware_version": machine.firmware_ui, "locale": used_locale},
                token,
            )
        )
    except DaedalusContentError as err:
        _LOGGER.debug("No drink photos: %s", err)
        photos = {}

    return [
        Drink(d.id, d.name, d.groups, *photos.get(d.id, (None, None)), values=d.values)
        for d in drinks
    ]
