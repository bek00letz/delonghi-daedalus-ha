"""Config flow for De'Longhi Daedalus integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import aiohttp_client

from .api.client import (
    DaedalusAuthError,
    DaedalusCloudClient,
    DaedalusConnectionError,
)
from .api.models import DaedalusAppliance
from .const import (
    CONF_AUTH_TOKEN,
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


class DaedalusConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for De'Longhi Daedalus."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._username: str | None = None
        self._password: str | None = None
        self._device_ip: str | None = None
        self._devices: list[DaedalusAppliance] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle initial authentication step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME].strip()
            self._password = user_input[CONF_PASSWORD]
            self._device_ip = user_input.get(CONF_DEVICE_IP, "").strip() or None

            session = aiohttp_client.async_get_clientsession(self.hass)
            client = DaedalusCloudClient(session=session)

            try:
                await client.async_login(self._username, self._password)
                await client.async_get_jwt()
                self._devices = await client.async_get_devices()
            except DaedalusAuthError:
                errors["base"] = "invalid_auth"
            except DaedalusConnectionError:
                errors["base"] = "cannot_connect"
            except Exception as err:
                _LOGGER.exception("Unexpected error during De'Longhi cloud discovery: %s", err)
                errors["base"] = "unknown"
            else:
                if not self._devices:
                    errors["base"] = "no_devices"
                elif len(self._devices) == 1:
                    return await self._create_entry_for_device(self._devices[0])
                else:
                    return await self.async_step_select_device()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_DEVICE_IP): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
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
            CONF_AUTH_TOKEN: device.auth_token,
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
