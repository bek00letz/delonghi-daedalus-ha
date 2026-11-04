"""Setup, sign-in-again and options screens for De'Longhi My Coffee Lounge."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api.client import (
    DaedalusAccountLockedError,
    DaedalusApiError,
    DaedalusAuthError,
    DaedalusCloudClient,
    DaedalusConnectionError,
)
from .api.cloud import async_read_reported_address
from .api.discovery import async_accepts_token, async_find_candidates
from .api.models import DaedalusAppliance
from .const import (
    CONF_CONFIRM_MACHINE,
    CONF_DEVICE_IP,
    CONF_FINGERPRINT_HANDOFF,
    CONF_MODEL,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_SERIAL_NUMBER,
    CONF_SESSION_HANDOFF,
    CONF_THING_NAME,
    CONF_TOKEN_HANDOFF,
    CONF_USERNAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)
from .local_network import async_find_local_candidates, is_local_address

_LOGGER = logging.getLogger(__name__)

# The autocomplete hints let browsers and password managers fill these in.
EMAIL_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)
CONF_MACHINE = "machine"


class MachineConfirmSteps:
    """Find the machine and have the person confirm it before it gets the token.

    Shared by setup and by the options screen, so both trust a machine the
    same way. Nothing is sent to any device until the person confirms one:

    1. ``search``: look for devices that look like a coffee machine (only a
       TLS handshake), or check the one address the person typed, and read
       the address the machine reports to De'Longhi's cloud.
    2. ``confirm_machine``: show what was found, with the cloud-reported
       address as evidence (a device on the home network can't fake it).
    3. Only then the account token goes to that one device, pinned to the
       certificate just seen, and the device is trusted if it accepts.
    """

    hass: Any
    _typed_address: str | None
    _candidates: list[tuple[str, bytes]]
    _reported_address: str | None
    _search: asyncio.Task[None] | None
    _asked_for_address: bool

    def _init_confirm(self) -> None:
        self._typed_address = None
        self._candidates = []
        self._reported_address = None
        self._search = None
        self._asked_for_address = False

    # Provided by the flow using these steps.
    _serial: str
    _thing_name: str

    async def _async_token(self) -> str:
        raise NotImplementedError

    async def _async_machine_confirmed(self, address: str, fingerprint: bytes) -> FlowResult:
        raise NotImplementedError

    async def _async_machine_skipped(self) -> FlowResult:
        raise NotImplementedError

    async def _async_look(self) -> None:
        session = aiohttp_client.async_get_clientsession(self.hass)
        try:
            self._reported_address = await async_read_reported_address(
                session, self._thing_name, await self._async_token()
            )
        except Exception:  # the evidence is helpful, not required
            _LOGGER.debug("Couldn't read the address the machine reports", exc_info=True)
            self._reported_address = None
        if self._typed_address:
            self._candidates = await async_find_candidates([self._typed_address])
        else:
            first = [self._reported_address] if self._reported_address else []
            self._candidates = await async_find_local_candidates(self.hass, first=first)
        # The device at the cloud-reported address first.
        self._candidates.sort(key=lambda c: c[0] != self._reported_address)

    async def async_step_search(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Look for the machine, with a progress screen while it runs."""
        if self._search is None:
            self._search = self.hass.async_create_task(self._async_look())
        if not self._search.done():
            return self.async_show_progress(  # type: ignore[attr-defined]
                step_id="search", progress_action="searching", progress_task=self._search
            )
        self._search = None
        next_step = "confirm_machine" if self._candidates else "local_address"
        return self.async_show_progress_done(next_step_id=next_step)  # type: ignore[attr-defined]

    async def async_step_confirm_machine(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Let the person pick the device to trust; only then send it the token."""
        errors: dict[str, str] = {}
        addresses = [host for host, _ in self._candidates]
        if user_input is not None and user_input.get(CONF_MACHINE) in addresses:
            host = user_input[CONF_MACHINE]
            fingerprint = dict(self._candidates)[host]
            if await async_accepts_token(
                aiohttp_client.async_get_clientsession(self.hass),
                host,
                fingerprint,
                self._serial,
                await self._async_token(),
            ):
                return await self._async_machine_confirmed(host, fingerprint)
            errors["base"] = "machine_refused"

        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="confirm_machine",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MACHINE, default=addresses[0]): SelectSelector(
                        SelectSelectorConfig(options=addresses, mode=SelectSelectorMode.LIST)
                    )
                }
            ),
            description_placeholders={"reported": self._reported_address or "—"},
            errors=errors,
        )

    async def async_step_local_address(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Nothing found (or none of them): type an address, or continue without."""
        errors: dict[str, str] = {}
        # Arriving from the search screen also passes an (empty) answer, so only
        # treat it as the person's answer once the form has actually been shown.
        if user_input is not None and self._asked_for_address:
            address = (user_input.get(CONF_DEVICE_IP) or "").strip()
            if not address:
                return await self._async_machine_skipped()
            if is_local_address(address):
                self._typed_address = address
                self._asked_for_address = False
                return await self.async_step_search()
            errors[CONF_DEVICE_IP] = "invalid_address"
        self._asked_for_address = True
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="local_address",
            data_schema=vol.Schema({vol.Optional(CONF_DEVICE_IP): str}),
            description_placeholders={"reported": self._reported_address or "—"},
            errors=errors,
        )


class DaedalusConfigFlow(MachineConfirmSteps, config_entries.ConfigFlow, domain=DOMAIN):
    """Sign in, choose the machine, and confirm it on the home network."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str | None = None
        self._devices: list[DaedalusAppliance] = []
        self._client: DaedalusCloudClient | None = None
        self._device: DaedalusAppliance | None = None
        self._address: str | None = None
        self._fingerprint: bytes | None = None
        self._init_confirm()

    @property
    def _serial(self) -> str:  # type: ignore[override]
        assert self._device is not None
        return self._device.serial_number

    @property
    def _thing_name(self) -> str:  # type: ignore[override]
        assert self._device is not None
        return self._device.machine_name

    async def _async_token(self) -> str:
        assert self._client is not None
        return await self._client.async_get_token()

    async def _async_sign_in(
        self, username: str, password: str
    ) -> tuple[list[DaedalusAppliance], dict[str, str]]:
        """Sign in and list appliances. The one check both sign-in screens use."""
        client = DaedalusCloudClient(session=aiohttp_client.async_get_clientsession(self.hass))
        try:
            await client.async_sign_in(username, password)
            devices = await client.async_get_devices()
            self._client = client
        except DaedalusAccountLockedError as err:
            _LOGGER.warning("De'Longhi paused sign-ins for this account: %s", err)
            return [], {"base": "account_locked"}
        except DaedalusAuthError as err:
            _LOGGER.warning("De'Longhi sign-in rejected: %s", err)
            return [], {"base": "invalid_auth"}
        except DaedalusConnectionError as err:
            _LOGGER.warning("Couldn't reach De'Longhi cloud during sign-in: %s", err)
            return [], {"base": "cannot_connect"}
        except DaedalusApiError as err:
            _LOGGER.warning("De'Longhi sign-in failed: %s", err)
            return [], {"base": "unknown"}
        except Exception:
            _LOGGER.exception("Unexpected error during De'Longhi sign-in")
            return [], {"base": "unknown"}
        return devices, {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Sign in. An address is optional: without one, the machine is searched for."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            address = (user_input.get(CONF_DEVICE_IP) or "").strip()
            if address and not is_local_address(address):
                errors[CONF_DEVICE_IP] = "invalid_address"
            else:
                self._typed_address = address or None
                self._devices, errors = await self._async_sign_in(
                    self._username, user_input[CONF_PASSWORD]
                )
            if not errors:
                if not self._devices:
                    errors["base"] = "no_devices"
                elif len(self._devices) == 1:
                    return await self._async_choose(self._devices[0])
                else:
                    return await self.async_step_select_device()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): EMAIL_SELECTOR,
                vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR,
                vol.Optional(CONF_DEVICE_IP): str,
            }
        )
        # Keep what was typed (except the password) when the form comes back with an error.
        suggested = {k: v for k, v in (user_input or {}).items() if k != CONF_PASSWORD}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> FlowResult:
        """Start signing in again after De'Longhi stopped accepting the saved sign-in."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for the current password; keep only the new session, never the password."""
        entry = self._get_reauth_entry()
        username: str = entry.data[CONF_USERNAME]
        errors: dict[str, str] = {}

        if user_input is not None:
            devices, errors = await self._async_sign_in(username, user_input[CONF_PASSWORD])
            if not errors:
                if any(d.serial_number == entry.unique_id for d in devices):
                    data = {k: v for k, v in entry.data.items() if k != CONF_PASSWORD}
                    return self.async_update_reload_and_abort(
                        entry, data={**data, **self._handoff()}
                    )
                errors["base"] = "device_not_on_account"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD_SELECTOR}),
            description_placeholders={"username": username, "name": entry.title},
            errors=errors,
        )

    async def async_step_select_device(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Choose the machine when several are linked to the account."""
        if user_input is not None:
            selected = user_input[CONF_SERIAL_NUMBER]
            device = next((d for d in self._devices if d.serial_number == selected), None)
            if device:
                return await self._async_choose(device)

        options = {d.serial_number: f"{d.display_name} ({d.serial_number})" for d in self._devices}
        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema({vol.Required(CONF_SERIAL_NUMBER): vol.In(options)}),
        )

    async def _async_choose(self, device: DaedalusAppliance) -> FlowResult:
        await self.async_set_unique_id(device.serial_number)
        self._abort_if_unique_id_configured()
        self._device = device
        return await self.async_step_search()

    async def _async_machine_confirmed(self, address: str, fingerprint: bytes) -> FlowResult:
        self._address, self._fingerprint = address, fingerprint
        return self._create_entry()

    async def _async_machine_skipped(self) -> FlowResult:
        # Set up without control over the home network; the options screen
        # can confirm the machine later.
        return self._create_entry()

    def _handoff(self) -> dict[str, Any]:
        """The secrets to pass to the integration once (moved to its private store)."""
        client = self._client
        assert client is not None and client.gigya_session is not None
        handoff: dict[str, Any] = {
            CONF_SESSION_HANDOFF: list(client.gigya_session),
            CONF_TOKEN_HANDOFF: client._jwt_token,
        }
        if self._fingerprint is not None:
            handoff[CONF_FINGERPRINT_HANDOFF] = self._fingerprint.hex()
        return handoff

    def _create_entry(self) -> FlowResult:
        device = self._device
        assert device is not None
        entry_data = {
            CONF_USERNAME: self._username,
            **self._handoff(),
            CONF_SERIAL_NUMBER: device.serial_number,
            CONF_THING_NAME: device.machine_name,
            CONF_MODEL: device.model,
            CONF_DEVICE_IP: self._address,
        }
        title = f"{device.display_name} ({device.serial_number[-6:]})"
        return self.async_create_entry(title=title, data=entry_data)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return DaedalusOptionsFlowHandler(config_entry)


class DaedalusOptionsFlowHandler(MachineConfirmSteps, config_entries.OptionsFlow):
    """Options: how often to check the cloud, and confirming the machine again."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry
        self._poll_interval = config_entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._init_confirm()

    @property
    def _coordinator(self) -> Any:
        return self.hass.data[DOMAIN][self._config_entry.entry_id]

    @property
    def _serial(self) -> str:  # type: ignore[override]
        return self._config_entry.data[CONF_SERIAL_NUMBER]

    @property
    def _thing_name(self) -> str:  # type: ignore[override]
        return self._config_entry.data[CONF_THING_NAME]

    async def _async_token(self) -> str:
        return await self._coordinator.cloud_client.async_get_token()

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._poll_interval = user_input[CONF_POLL_INTERVAL]
            if user_input.get(CONF_CONFIRM_MACHINE):
                return await self.async_step_search()
            return self.async_create_entry(
                title="", data={**self._config_entry.options, CONF_POLL_INTERVAL: self._poll_interval}
            )

        schema = vol.Schema(
            {
                vol.Optional(CONF_POLL_INTERVAL, default=self._poll_interval): vol.All(
                    vol.Coerce(int), vol.Range(min=10, max=300)
                ),
                vol.Optional(CONF_CONFIRM_MACHINE, default=False): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def _async_machine_confirmed(self, address: str, fingerprint: bytes) -> FlowResult:
        store = self._coordinator.store
        store.set_machine_fingerprint(fingerprint)
        await store.async_save_now()
        result = self.async_create_entry(
            title="",
            data={
                **self._config_entry.options,
                CONF_POLL_INTERVAL: self._poll_interval,
                CONF_DEVICE_IP: address,
            },
        )
        # Reload even when the address didn't change, so the new trust applies now.
        self.hass.config_entries.async_schedule_reload(self._config_entry.entry_id)
        return result

    async def _async_machine_skipped(self) -> FlowResult:
        return self.async_create_entry(
            title="", data={**self._config_entry.options, CONF_POLL_INTERVAL: self._poll_interval}
        )
