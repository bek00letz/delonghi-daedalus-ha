"""DataUpdateCoordinator for De'Longhi Daedalus integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.client import (
    DaedalusApiError,
    DaedalusAuthError,
    DaedalusCloudClient,
    DaedalusConnectionError,
)
from .api.models import (
    BeverageParameters,
    BeverageReport,
    DaedalusAppliance,
    MachineSettings,
    MachineState,
    MachineStatus,
)
from .api.websocket import DaedalusCommandError, DaedalusLocalWebSocket
from .const import (
    CONF_DEVICE_IP,
    CONF_MODEL,
    CONF_SERIAL_NUMBER,
    CONF_THING_NAME,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    RESPONSE_BEVERAGE_REPORT,
    RESPONSE_BEVERAGE_REQUEST,
    RESPONSE_MACHINE_UI,
    RESPONSE_OK,
    RESPONSE_SET_SETTINGS,
    STATE_BREWING,
    STATE_OFFLINE,
    STATE_READY,
    STATE_STANDBY,
    TURN_ON_OFF_STANDBY,
    TURN_ON_OFF_WAKE,
)

_LOGGER = logging.getLogger(__name__)


class DaedalusCoordinator(DataUpdateCoordinator[MachineState]):
    """Coordinates updates and commands for a De'Longhi Daedalus appliance."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        cloud_client: DaedalusCloudClient,
        appliance: DaedalusAppliance,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
    ) -> None:
        """Initialize Daedalus coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{appliance.serial_number}",
            update_interval=timedelta(seconds=poll_interval),
        )
        self.hass = hass
        self.cloud_client = cloud_client
        self.appliance = appliance
        self.state = MachineState()

        self._local_ws: DaedalusLocalWebSocket | None = None
        self._unregister_ws_cb: Any | None = None

        if appliance.local_ip:
            self._init_local_websocket(appliance.local_ip)

    def _init_local_websocket(self, host: str) -> None:
        """Initialize and configure local LAN2LAN WebSocket connection."""
        self._local_ws = DaedalusLocalWebSocket(
            host=host,
            serial_number=self.appliance.serial_number,
            token_provider=self.cloud_client.async_get_token,
        )
        self._unregister_ws_cb = self._local_ws.register_callback(self._handle_ws_message)

    async def async_start(self) -> None:
        """Start local WebSocket listener and initial coordinator refresh."""
        # Check the cloud sign-in first: if it fails, setup stops before any
        # local connection is opened, so nothing is left running.
        await self.async_config_entry_first_refresh()
        if self._local_ws:
            await self._local_ws.async_start()

    async def async_stop(self) -> None:
        """Stop local WebSocket and release network resources."""
        if self._unregister_ws_cb:
            self._unregister_ws_cb()
            self._unregister_ws_cb = None
        if self._local_ws:
            await self._local_ws.async_stop()
            self._local_ws = None
        await self.cloud_client.async_close()

    def update_local_ip(self, new_ip: str) -> None:
        """Update target IP address of the appliance."""
        self.appliance.local_ip = new_ip
        if self._local_ws:
            self._local_ws.update_host(new_ip)
        else:
            self._init_local_websocket(new_ip)
            if self._local_ws:
                self.hass.async_create_task(self._local_ws.async_start())

    def _handle_ws_message(self, data: dict[str, Any]) -> None:
        """Handle incoming asynchronous event messages from appliance."""
        msg_type = data.get("Message")

        # 0. The machine's own status report: the source of truth when it comes.
        if (status := MachineStatus.from_message(data)) is not None:
            self.state.update_from_status(status)
            self.async_set_updated_data(self.state)

        # 1. Power / Standby transition report (only an OK means it happened)
        elif msg_type == RESPONSE_MACHINE_UI:
            turn_on_off = data.get("TurnOnOff")
            if turn_on_off is not None and data.get("Response") == RESPONSE_OK:
                self.state.update_from_ui_response(int(turn_on_off))
                _LOGGER.debug("Power state updated via WebSocket: %s", self.state.is_on)
                self.async_set_updated_data(self.state)

        # 2. Drink brewing progress or report
        elif msg_type == RESPONSE_BEVERAGE_REQUEST:
            if data.get("Response") == RESPONSE_OK:
                self.state.operational_state = STATE_BREWING
                self.async_set_updated_data(self.state)

        elif msg_type == RESPONSE_BEVERAGE_REPORT:
            report = BeverageReport.from_dict(data)
            self.state.last_drink = report
            self.state.operational_state = STATE_READY
            _LOGGER.info("Beverage brewed: %s (Recipe %s)", report.recipe_name, report.recipe_id)
            self.async_set_updated_data(self.state)

        # 3. Settings configuration response
        elif msg_type == RESPONSE_SET_SETTINGS:
            _LOGGER.debug("Appliance settings successfully acknowledged")

        # Update local connectivity flag
        self.state.is_local_connected = self._local_ws.is_connected if self._local_ws else False

    async def _async_update_data(self) -> MachineState:
        """Fetch latest status from cloud and verify local socket."""
        try:
            # Query cloud for device online status and metadata
            devices = await self.cloud_client.async_get_devices()
            target_device = next(
                (d for d in devices if d.serial_number == self.appliance.serial_number),
                None,
            )

            if target_device:
                self.state.is_cloud_online = target_device.status.upper() == "ONLINE"
                self.appliance.status = target_device.status
                if not self.state.is_cloud_online and not self.state.is_local_connected:
                    self.state.operational_state = STATE_OFFLINE
                elif self.state.operational_state == STATE_OFFLINE:
                    self.state.operational_state = STATE_STANDBY if not self.state.is_on else STATE_READY

        except DaedalusAuthError as err:
            raise ConfigEntryAuthFailed(f"De'Longhi no longer accepts the saved password: {err}") from err
        except (DaedalusConnectionError, DaedalusApiError) as err:
            _LOGGER.debug("Cloud poll failed (continuing with local state): %s", err)

        # Update local connection state
        if self._local_ws:
            self.state.is_local_connected = self._local_ws.is_connected
            if self.state.is_local_connected and self.state.operational_state == STATE_OFFLINE:
                self.state.operational_state = STATE_STANDBY if not self.state.is_on else STATE_READY

        return self.state

    async def _async_command(self, send: Callable[[DaedalusLocalWebSocket], Awaitable[Any]]) -> None:
        """Run one local command; turn a failure into a message the person can act on."""
        if not self._local_ws:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="command_no_local_address"
            )
        try:
            await send(self._local_ws)
        except DaedalusCommandError as err:
            _LOGGER.debug("Local command failed: %s", err)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key=f"command_{err.reason}"
            ) from err

    async def async_turn_on(self) -> None:
        """Wake the coffee machine from standby."""
        await self._async_command(lambda ws: ws.async_turn_on())
        self.state.update_from_ui_response(TURN_ON_OFF_WAKE)
        self.async_set_updated_data(self.state)

    async def async_turn_off(self) -> None:
        """Put the coffee machine into standby."""
        await self._async_command(lambda ws: ws.async_turn_off())
        self.state.update_from_ui_response(TURN_ON_OFF_STANDBY)
        self.async_set_updated_data(self.state)

    async def async_brew(
        self,
        recipe_id: int,
        profile_id: int = 1,
        parameters: BeverageParameters | None = None,
    ) -> None:
        """Start brewing a drink."""
        await self._async_command(lambda ws: ws.async_brew(recipe_id, profile_id, parameters))
        self.state.operational_state = STATE_BREWING
        self.async_set_updated_data(self.state)

    async def async_set_settings(self, settings: MachineSettings) -> None:
        """Change machine settings."""
        await self._async_command(lambda ws: ws.async_set_settings(settings))
        if settings.water_temperature is not None:
            self.state.settings.water_temperature = settings.water_temperature
        if settings.auto_off is not None:
            self.state.settings.auto_off = settings.auto_off
        if settings.water_hardness is not None:
            self.state.settings.water_hardness = settings.water_hardness
        self.async_set_updated_data(self.state)
