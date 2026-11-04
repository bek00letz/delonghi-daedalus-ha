"""Config flow for De'Longhi Daedalus integration."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api.client import (
    DaedalusApiError,
    DaedalusAuthError,
    DaedalusCloudClient,
    DaedalusConnectionError,
)
from .api.models import DaedalusAppliance
from .const import (
    CONF_DEVICE_IP,
    CONF_MODEL,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_SERIAL_NUMBER,
    CONF_THING_NAME,
    CONF_USERNAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# The autocomplete hints let browsers and password managers fill these in.
EMAIL_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
)
PASSWORD_SELECTOR = TextSelector(
    TextSelectorConfig(type=TextSelectorType.PASSWORD, autocomplete="current-password")
)


class DaedalusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for De'Longhi Daedalus."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._username: str | None = None
        self._password: str | None = None
        self._device_ip: str | None = None
        self._devices: list[DaedalusAppliance] = []

    async def _async_sign_in(
        self, username: str, password: str
    ) -> tuple[list[DaedalusAppliance], dict[str, str]]:
        """Sign in and list appliances. The one check both sign-in screens use.

        Returns the appliances found and the form errors to show, if any.
        """
        client = DaedalusCloudClient(session=aiohttp_client.async_get_clientsession(self.hass))
        try:
            await client.async_authenticate(username, password)
            devices = await client.async_get_devices()
        except DaedalusAuthError as err:
            _LOGGER.info("De'Longhi sign-in rejected: %s", err)
            return [], {"base": "invalid_auth"}
        except (DaedalusConnectionError, DaedalusApiError) as err:
            _LOGGER.warning("Couldn't reach De'Longhi cloud during sign-in: %s", err)
            return [], {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error during De'Longhi sign-in")
            return [], {"base": "unknown"}
        return devices, {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle initial authentication step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            self._device_ip = user_input.get(CONF_DEVICE_IP, "").strip() or None

            self._devices, errors = await self._async_sign_in(self._username, self._password)
            if not errors:
                if not self._devices:
                    errors["base"] = "no_devices"
                elif len(self._devices) == 1:
                    return await self._create_entry_for_device(self._devices[0])
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
        """Start signing in again after De'Longhi stopped accepting the saved password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Ask for the current password for the account this machine is set up with."""
        entry = self._get_reauth_entry()
        username: str = entry.data[CONF_USERNAME]
        errors: dict[str, str] = {}

        if user_input is not None:
            password = user_input[CONF_PASSWORD]
            devices, errors = await self._async_sign_in(username, password)
            if not errors:
                if any(d.serial_number == entry.unique_id for d in devices):
                    return self.async_update_reload_and_abort(
                        entry, data_updates={CONF_PASSWORD: password}
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
        """Allow user to select an appliance if multiple are linked to the account."""
        if user_input is not None:
            selected_sn = user_input[CONF_SERIAL_NUMBER]
            device = next((d for d in self._devices if d.serial_number == selected_sn), None)
            if device:
                return await self._create_entry_for_device(device)

        options = {d.serial_number: f"{d.display_name} ({d.serial_number})" for d in self._devices}
        return self.async_show_form(
            step_id="select_device",
            data_schema=vol.Schema({vol.Required(CONF_SERIAL_NUMBER): vol.In(options)}),
        )

    async def _create_entry_for_device(self, device: DaedalusAppliance) -> FlowResult:
        """Create configuration entry for the specified appliance."""
        await self.async_set_unique_id(device.serial_number)
        self._abort_if_unique_id_configured()

        entry_data = {
            CONF_USERNAME: self._username,
            CONF_PASSWORD: self._password,
            CONF_SERIAL_NUMBER: device.serial_number,
            CONF_THING_NAME: device.machine_name,
            CONF_MODEL: device.model,
            CONF_DEVICE_IP: self._device_ip,
        }

        title = f"{device.display_name} ({device.serial_number[-6:]})"
        return self.async_create_entry(title=title, data=entry_data)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow handler for this entry."""
        return DaedalusOptionsFlowHandler(config_entry)


class DaedalusOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for De'Longhi Daedalus."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage integration options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current_ip = self._config_entry.options.get(
            CONF_DEVICE_IP, self._config_entry.data.get(CONF_DEVICE_IP, "")
        )
        current_poll = self._config_entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )

        schema = vol.Schema(
            {
                vol.Optional(CONF_DEVICE_IP, default=current_ip or ""): str,
                vol.Optional(CONF_POLL_INTERVAL, default=current_poll): vol.All(
                    vol.Coerce(int), vol.Range(min=10, max=300)
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)

