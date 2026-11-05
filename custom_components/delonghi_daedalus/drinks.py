"""The drinks one machine offers: one list for buttons, the last-drink name and the card.

The list comes from De'Longhi's content server (names in Home Assistant's
language where offered) and is kept in the private store, so it survives a
restart without internet. Each drink's photo is downloaded once into Home
Assistant's cache folder and served from there; the browser never contacts
De'Longhi. Until the first list arrives, a short built-in list of drinks
confirmed on an Eletta Ultra stands in.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import hashlib
import logging
import os
from pathlib import Path
import shutil
import time

import aiohttp

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .api.client import DaedalusApiError, DaedalusAuthError, DaedalusConnectionError
from .api.content import DaedalusContentError, Drink, MachineIdentity, async_fetch_drinks
from . import recipes
from .const import (
    DOMAIN,
    RECIPE_AMERICANO,
    RECIPE_DOPPIO_PLUS,
    RECIPE_ESPRESSO,
    RECIPE_HOT_WATER,
    RECIPE_LATTE_MACCHIATO,
    RECIPE_LONG,
    RECIPE_NAMES,
    RECIPE_SIMPLE_COFFEE,
)
from .storage import DaedalusStore

_LOGGER = logging.getLogger(__name__)

PHOTO_URL_BASE = f"/{DOMAIN}/drinks"
LIST_FORMAT = "2"  # 2: each drink's values (0.10)
REFRESH_EVERY = 7 * 24 * 3600  # look for new drinks or new names weekly
RETRY_AFTER = 3600  # after a failed attempt
MAX_PHOTO_BYTES = 2 * 1024 * 1024
PHOTO_TIMEOUT = aiohttp.ClientTimeout(total=30)
PHOTO_DOWNLOADS_AT_ONCE = 4
PNG_START = b"\x89PNG\r\n\x1a\n"
PNG_END = b"\x00\x00\x00\x00IEND\xaeB`\x82"  # the closing chunk: a file without it is cut off


def complete_png(data: bytes) -> bool:
    """A whole PNG file: starts with the signature and ends with the closing chunk."""
    return data.startswith(PNG_START) and data.endswith(PNG_END)


def _version(data: bytes) -> str:
    """Changes with the file, so browsers never keep showing an older (or broken) copy."""
    return hashlib.sha256(data).hexdigest()[:10]

# Stands in until De'Longhi's list arrives; numbers confirmed on an ECAM 470.85.
BUILT_IN_DRINKS = tuple(
    Drink(recipe, RECIPE_NAMES[recipe], frozenset({"HOT", "COFFEE"} if recipe != RECIPE_HOT_WATER else {"HOT"}))
    for recipe in (
        RECIPE_ESPRESSO,
        RECIPE_SIMPLE_COFFEE,
        RECIPE_LONG,
        RECIPE_DOPPIO_PLUS,
        RECIPE_AMERICANO,
        RECIPE_LATTE_MACCHIATO,
        RECIPE_HOT_WATER,
    )
)


def photos_root(hass: HomeAssistant) -> Path:
    """Home Assistant's cache folder for this integration (not backed up; can be re-downloaded)."""
    return Path(hass.config.path(".cache", DOMAIN))


async def async_serve_photos(hass: HomeAssistant) -> None:
    """Serve cached drink photos at /delonghi_daedalus/drinks/…, once per run.

    They are De'Longhi's public product photos, so like /local/ they need no sign-in.
    """
    done: set[str] = hass.data.setdefault(f"{DOMAIN}_web", set())
    if "photos" in done or hass.http is None:
        return
    done.add("photos")
    root = photos_root(hass)
    await hass.async_add_executor_job(lambda: root.mkdir(parents=True, exist_ok=True))
    await hass.http.async_register_static_paths([StaticPathConfig(PHOTO_URL_BASE, str(root), True)])


class DrinkList:
    """The drinks of one machine, kept up to date and stored."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        store: DaedalusStore | None,
        session: aiohttp.ClientSession,
    ) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._store = store
        self._session = session
        self._folder = photos_root(hass) / entry_id
        self._listeners: list[Callable[[], None]] = []
        self._photos: dict[int, tuple[str, str]] = {}  # drink → (source link, version) of whole files on disk
        self._last_attempt = 0.0
        self._task: asyncio.Task | None = None
        saved = (store.drinks if store else {}).get("drinks")
        loaded = [d for d in (Drink.from_dict(x) for x in saved or [] if isinstance(x, dict)) if d]
        self.from_delonghi = bool(loaded)
        self._drinks = {d.id: d for d in (loaded or BUILT_IN_DRINKS)}

    @property
    def drinks(self) -> list[Drink]:
        return list(self._drinks.values())

    def get(self, drink_id: int) -> Drink | None:
        return self._drinks.get(drink_id)

    def name(self, drink_id: int) -> str:
        """A drink's name, for any number the machine reports."""
        drink = self._drinks.get(drink_id)
        if drink and drink.name:
            return drink.name
        return RECIPE_NAMES.get(drink_id, f"Drink {drink_id}")

    def photo(self, drink_id: int) -> str | None:
        """The local address of a drink's photo, if it's been downloaded."""
        photo = self._photos.get(drink_id)
        if photo is None:
            return None
        return f"{PHOTO_URL_BASE}/{self._entry_id}/{drink_id}.png?v={photo[1]}"

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` whenever the list or a photo changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def async_load_photos(self) -> None:
        """Note which saved photos are on disk and whole; others download again.

        The cache folder can be cleared, and 0.8.0 saved photos cut off.
        """
        saved = self._store.photos if self._store else {}
        on_disk = await self._hass.async_add_executor_job(self._files_on_disk)
        self._photos = {
            int(k): (source, on_disk[int(k)])
            for k, source in saved.items()
            if k.isdigit() and int(k) in on_disk
        }

    def _files_on_disk(self) -> dict[int, str]:
        """{drink: version} for each whole photo in the folder."""
        try:
            names = os.listdir(self._folder)
        except OSError:
            return {}
        found: dict[int, str] = {}
        for name in names:
            if not (name.endswith(".png") and name[:-4].isdigit()):
                continue
            try:
                data = (self._folder / name).read_bytes()
            except OSError:
                continue
            if complete_png(data):
                found[int(name[:-4])] = _version(data)
        return found

    @callback
    def maybe_refresh(
        self,
        machine: MachineIdentity,
        token: Callable[[], Awaitable[str]],
        language: str | None,
        country: str | None,
    ) -> None:
        """Fetch the list in the background when it's missing, out of date or for other firmware."""
        if not machine.complete or (self._task and not self._task.done()):
            return
        # LIST_FORMAT changes when more is read from the list, so saved lists are read again.
        key = [LIST_FORMAT, machine.model, machine.sku, machine.firmware_ui, machine.firmware_wifi,
               machine.firmware_mainboard, language or "", country or ""]
        saved = self._store.drinks if self._store else {}
        now = time.time()
        fresh = saved.get("key") == key and now - float(saved.get("fetched") or 0) < REFRESH_EVERY
        missing_photos = any(d.photo_url and d.id not in self._photos for d in self._drinks.values())
        if fresh and not missing_photos:
            return
        if now - self._last_attempt < RETRY_AFTER:
            return
        self._last_attempt = now
        self._task = self._hass.async_create_background_task(
            self._async_refresh(machine, token, language, country, key, fresh),
            f"{DOMAIN} drink list",
        )

    async def _async_refresh(self, machine, token, language, country, key, fresh: bool) -> None:
        if not fresh:
            try:
                drinks = await async_fetch_drinks(self._session, await token(), machine, language, country)
            except (DaedalusContentError, DaedalusConnectionError, DaedalusApiError, DaedalusAuthError) as err:
                _LOGGER.info("Couldn't get the machine's drink list from De'Longhi; will try again later (%s)", err)
                return
            self._drinks = {d.id: d for d in drinks}
            self.from_delonghi = True
            if self._store:
                self._store.set_drinks({"key": key, "fetched": time.time(), "drinks": [d.as_dict() for d in drinks]})
            self._notify()
        if await self._async_download_photos():
            self._notify()

    async def _async_download_photos(self) -> bool:
        """Download photos that are new or changed; True if any arrived."""
        wanted = {d.id: d.photo_url for d in self._drinks.values() if d.photo_url}
        todo = {k: v for k, v in wanted.items() if self._photos.get(k, ("",))[0] != v}
        if not todo:
            return False
        await self._hass.async_add_executor_job(lambda: self._folder.mkdir(parents=True, exist_ok=True))
        gate = asyncio.Semaphore(PHOTO_DOWNLOADS_AT_ONCE)

        async def one(drink_id: int, url: str) -> None:
            async with gate:
                data = await self._async_fetch_photo(url)
            if data is not None:
                await self._hass.async_add_executor_job(self._write, drink_id, data)
                self._photos[drink_id] = (url, _version(data))

        # One photo failing in an unexpected way mustn't stop the others.
        for failure in await asyncio.gather(*(one(k, v) for k, v in todo.items()), return_exceptions=True):
            if isinstance(failure, Exception):
                _LOGGER.debug("A drink photo couldn't be saved: %r", failure)
        if self._store:
            self._store.set_photos({str(k): v[0] for k, v in self._photos.items()})
        return any(self._photos.get(k, ("",))[0] == v for k, v in todo.items())

    async def _async_fetch_photo(self, url: str) -> bytes | None:
        """One whole photo, only if it's a complete PNG of reasonable size."""
        data = bytearray()
        try:
            async with self._session.get(url, timeout=PHOTO_TIMEOUT) as resp:
                if resp.status != 200 or resp.content_type != "image/png":
                    return None
                length = resp.headers.get("Content-Length", "")
                if length.isdigit() and int(length) > MAX_PHOTO_BYTES:
                    return None
                # A photo arrives in pieces: read to the end, not just the first piece.
                async for piece in resp.content.iter_chunked(64 * 1024):
                    data += piece
                    if len(data) > MAX_PHOTO_BYTES:
                        return None
        except (aiohttp.ClientError, TimeoutError):
            return None
        return bytes(data) if complete_png(data) else None

    def _write(self, drink_id: int, data: bytes) -> None:
        target = self._folder / f"{drink_id}.png"
        partial = target.with_suffix(".part")
        partial.write_bytes(data)
        partial.replace(target)

    # ---- recipes ----

    def recipe(self, drink_id: int, profile_id: int) -> dict[str, int]:
        """The values to send for this drink with this profile (see recipes.py)."""
        return recipes.recipe(self._drinks.get(drink_id), self._reported(profile_id, drink_id), self._personal(drink_id))

    def controls(self, drink_id: int, profile_id: int) -> list[dict]:
        """What the card's sheet can change for this drink."""
        return recipes.controls(self._drinks.get(drink_id), self._reported(profile_id, drink_id), self._personal(drink_id))

    @callback
    def set_personal(self, drink_id: int, changes: dict | None) -> None:
        """Save changes for a drink (None or empty: back to how the machine makes it)."""
        if self._store is None:
            return
        cleaned = recipes.clean_personal(self._drinks.get(drink_id), changes or {})
        self._store.set_personal(drink_id, cleaned or None)
        self._notify()

    @callback
    def record_report(self, profile_id: int, drink_id: int, report: dict) -> None:
        """Remember how the machine made a drink, from its report."""
        values = recipes.machine_values(report)
        if self._store is not None and values:
            self._store.set_reported(profile_id, drink_id, values)

    def _reported(self, profile_id: int, drink_id: int) -> dict[str, int]:
        return (self._store.reported if self._store else {}).get(f"{profile_id}:{drink_id}", {})

    def _personal(self, drink_id: int) -> dict[str, int]:
        return (self._store.personal if self._store else {}).get(str(drink_id), {})

    async def async_stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()

    async def async_remove(self) -> None:
        """Delete this machine's photos (when the integration is removed)."""
        await self._hass.async_add_executor_job(shutil.rmtree, self._folder, True)
