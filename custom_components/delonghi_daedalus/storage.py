"""Per-machine private storage: the account token and the machine's certificate.

Kept in Home Assistant's own storage (``.storage/delonghi_daedalus.<entry_id>``),
not in the config entry, so renewing the token never reloads the integration.
"""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import DOMAIN

STORAGE_VERSION = 1
SAVE_DELAY = 1  # seconds; coalesces bursts of changes into one write


class DaedalusStore:
    """Token and certificate fingerprint for one coffee machine."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        """Initialize the store for a config entry."""
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry_id}", private=True
        )
        self._data: dict[str, Any] = {}

    async def async_load(self) -> None:
        """Read what was saved last time."""
        self._data = await self._store.async_load() or {}

    @property
    def token(self) -> str | None:
        """The saved account token, if any."""
        return self._data.get("token")

    @property
    def machine_fingerprint(self) -> bytes | None:
        """SHA-256 of the machine's TLS certificate, recorded when it was first trusted."""
        value = self._data.get("machine_cert_sha256")
        return bytes.fromhex(value) if value else None

    @property
    def capabilities(self) -> dict | None:
        """The machine's capabilities record, as last read from the cloud."""
        return self._data.get("capabilities")

    @callback
    def set_capabilities(self, reported: dict) -> None:
        """Keep the capabilities so device details survive a restart without internet."""
        if self._data.get("capabilities") != reported:
            self._data["capabilities"] = reported
            self._save()

    @property
    def gigya_session(self) -> tuple[str, str] | None:
        """The saved De'Longhi sign-in session (token, secret); never the password."""
        value = self._data.get("session")
        return (value[0], value[1]) if isinstance(value, list) and len(value) == 2 else None

    @callback
    def set_gigya_session(self, session: tuple[str, str]) -> None:
        self._data["session"] = list(session)
        self._save()

    @property
    def filter(self) -> dict:
        """Last filter reading and when a change was last seen: {count, usage, changed}."""
        return dict(self._data.get("filter", {}))

    @callback
    def set_filter(self, value: dict) -> None:
        if self._data.get("filter") != value:
            self._data["filter"] = value
            self._save()

    @property
    def profiles(self) -> list[int]:
        """Profiles this machine has been seen using."""
        return list(self._data.get("profiles", []))

    @callback
    def set_profiles(self, profiles: list[int]) -> None:
        if self._data.get("profiles") != profiles:
            self._data["profiles"] = profiles
            self._save()

    @callback
    def set_token(self, token: str) -> None:
        """Remember a new account token."""
        self._data["token"] = token
        self._save()

    @callback
    def set_machine_fingerprint(self, fingerprint: bytes | None) -> None:
        """Trust a machine certificate (or forget it, with None)."""
        if fingerprint is None:
            self._data.pop("machine_cert_sha256", None)
        else:
            self._data["machine_cert_sha256"] = fingerprint.hex()
        self._save()

    async def async_save_now(self) -> None:
        """Write any pending change at once, e.g. before the integration reloads."""
        await self._store.async_save(dict(self._data))

    async def async_remove(self) -> None:
        """Delete everything stored for this machine."""
        await self._store.async_remove()

    def _save(self) -> None:
        self._store.async_delay_save(lambda: dict(self._data), SAVE_DELAY)
