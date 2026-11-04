"""DataUpdateCoordinator for De'Longhi Daedalus integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta
import time
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, issue_registry as ir
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

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
from .api.cloud_status import CloudStatusError, async_fetch_status
from .api.discovery import async_find_machine
from .api.websocket import DaedalusCommandError, DaedalusLocalWebSocket
from .local_network import async_find_local_machine
from .storage import DaedalusStore

# Search for the machine after it's been out of reach this long, at most this often.
SEARCH_AFTER = 5 * 60
SEARCH_EVERY = 30 * 60
# While there's no status yet, read the cloud's stored copy at most this often.
CLOUD_STATUS_EVERY = 5 * 60
from .const import (
    CONF_DEVICE_IP,
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
        store: DaedalusStore | None = None,
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
        self.store = store
        self._unreachable_since: float | None = None
        self._last_search = 0.0
        self._last_cloud_status = -CLOUD_STATUS_EVERY
        self._ip_hint: str | None = None

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
            fingerprint=self.store.machine_fingerprint if self.store else None,
            on_fingerprint=self.store.set_machine_fingerprint if self.store else None,
            on_identity_changed=self._identity_changed,
        )
        self._unregister_ws_cb = self._local_ws.register_callback(self._handle_ws_message)

    def _identity_changed(self, mismatch: bool) -> None:
        """Show (or clear) a repair notice when the machine's certificate changes."""
        issue_id = f"machine_identity_changed_{self.appliance.serial_number}"
        if not mismatch:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="machine_identity_changed",
            translation_placeholders={"host": self.appliance.local_ip or ""},
        )

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
        await self.cloud_client.async_renew_if_due()
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
                self._update_reachability()

        except DaedalusAuthError as err:
            raise ConfigEntryAuthFailed(f"De'Longhi no longer accepts the saved password: {err}") from err
        except (DaedalusConnectionError, DaedalusApiError) as err:
            _LOGGER.debug("Cloud poll failed (continuing with local state): %s", err)

        if self._local_ws:
            self.state.is_local_connected = self._local_ws.is_connected
        await self._maybe_fetch_cloud_status()
        self._update_reachability()
        self._maybe_search_for_machine()
        return self.state

    async def _maybe_fetch_cloud_status(self) -> None:
        """Until the machine reports locally, show the status it last reported to the cloud.

        The machine only reports on changes, so right after a restart this is
        the only way to know its status. Needs the internet; at most every
        5 minutes while there's still no status.
        """
        now = time.monotonic()
        if (
            self.state.status is not None
            or now - self._last_cloud_status < CLOUD_STATUS_EVERY
            or not self.cloud_client.token_valid()
        ):
            return
        self._last_cloud_status = now
        try:
            reported = await async_fetch_status(
                aiohttp_client.async_get_clientsession(self.hass),
                self.appliance.machine_name,
                await self.cloud_client.async_get_token(),
            )
        except (CloudStatusError, DaedalusConnectionError, DaedalusApiError, DaedalusAuthError) as err:
            _LOGGER.debug("Couldn't read the stored status from the cloud: %s", err)
            return
        status = MachineStatus.from_report(reported)
        if status is None:
            return
        self._ip_hint = status.reported_ip
        if self.state.status is None:  # a local report may have arrived meanwhile
            self.state.update_from_status(status)

    def _maybe_search_for_machine(self) -> None:
        """Look for the machine on the network when it isn't where we expect it.

        Runs when no address is set, or after the machine has been unreachable
        at its address (or answered with the wrong certificate) for a while,
        e.g. because the router gave it a new address. At most every 30 minutes.
        """
        now = time.monotonic()
        lost = self._local_ws is None or (
            not self._local_ws.is_connected or self._local_ws.identity_mismatch
        )
        if not lost:
            self._unreachable_since = None
            return
        self._unreachable_since = self._unreachable_since or now
        waited = self._local_ws is None or now - self._unreachable_since >= SEARCH_AFTER
        if waited and now - self._last_search >= SEARCH_EVERY and self.config_entry:
            self._last_search = now
            self.config_entry.async_create_background_task(
                self.hass, self._async_search_for_machine(), "delonghi_daedalus search"
            )

    async def _async_search_for_machine(self) -> None:
        fingerprint = self.store.machine_fingerprint if self.store else None
        if fingerprint is None and not self.cloud_client.token_valid():
            return  # can't confirm a candidate without a token
        found = None
        if self._ip_hint and fingerprint and self._ip_hint != self.appliance.local_ip:
            # Try the address the machine reported to the cloud first; the
            # certificate still has to match.
            found = await async_find_machine(
                aiohttp_client.async_get_clientsession(self.hass),
                [self._ip_hint],
                self.appliance.serial_number,
                fingerprint=fingerprint,
            )
        found = found or await async_find_local_machine(
            self.hass,
            self.appliance.serial_number,
            fingerprint=fingerprint,
            token_provider=None if fingerprint else self.cloud_client.async_get_token,
        )
        if found is None or found[0] == self.appliance.local_ip or self.config_entry is None:
            return
        _LOGGER.info("Found the coffee machine at a new address: %s", found[0])
        # Saving the address reloads the integration, which connects there.
        self.hass.config_entries.async_update_entry(
            self.config_entry, options={**self.config_entry.options, CONF_DEVICE_IP: found[0]}
        )

    def _update_reachability(self) -> None:
        """Mark the machine offline when neither path reaches it; never guess on or off."""
        reachable = self.state.is_cloud_online or self.state.is_local_connected
        if not reachable:
            self.state.operational_state = STATE_OFFLINE
        elif self.state.operational_state == STATE_OFFLINE:
            # Back in reach: the old state is stale until the machine reports again.
            self.state.operational_state = None
            if self.state.status is None:
                self.state.is_on = None

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
