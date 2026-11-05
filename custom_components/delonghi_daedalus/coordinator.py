"""DataUpdateCoordinator for De'Longhi Daedalus integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import dataclasses
from datetime import datetime, timedelta
import time
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import aiohttp_client, device_registry as dr, issue_registry as ir
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.util import dt as dt_util
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api.client import (
    DaedalusApiError,
    DaedalusAuthError,
    DaedalusCloudClient,
    DaedalusConnectionError,
)
from .api.models import (
    STATUS_BUSY,
    BeverageReport,
    DaedalusAppliance,
    MachineCapabilities,
    MachineSettings,
    MachineState,
    MachineStatus,
)
from .api.cloud import DaedalusCloudConnection
from .api.content import MachineIdentity
from .api.discovery import async_find_machine
from .api.websocket import DaedalusCommandError, DaedalusLocalWebSocket
from .drinks import DrinkList
from .local_network import async_find_local_machine
from .storage import DaedalusStore

# Search for the machine after it's been out of reach this long, at most this often.
SEARCH_AFTER = 5 * 60
SEARCH_EVERY = 30 * 60
# Alarms that stop Home Assistant from starting a drink.
BREW_BLOCKING_ALARMS = (
    "TankLevel", "TankPresence", "Waste", "NoCoffee", "General",
    "BrewUnitClean", "Infuser", "BrokenGrinder", "HeaterNTC",
)
# A filter-used percentage that drops this much, to at most this, means a new filter.
FILTER_RESET_DROP = 20
FILTER_RESET_BELOW = 5

# Settings the machine accepts at the top level of setMachineSettings, and
# where each lives in MachineSettings.
SETTING_FIELDS = {
    "CoffeeTemp": "coffee_temperature",
    "AutoTurnOffTimeInMin": "auto_off_minutes",
    "WaterHardness": "water_hardness",
}
from .const import (
    CONF_DEVICE_IP,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MANUFACTURER,
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
        self._ip_hint: str | None = None
        # Profile chosen in Home Assistant for its drinks; None = the machine's active one.
        self.brew_profile: int | None = None
        # Profiles seen in use (active on the machine, or in a brew report). The
        # machine supports several, but which exist isn't readable, so only
        # profiles known to be real are offered.
        self.known_profiles: set[int] = set(store.profiles) if store else set()
        if store and (cached := MachineCapabilities.from_report(store.capabilities)):
            self.state.capabilities = cached
        # The drinks this machine offers, with names and photos from De'Longhi.
        self.drinks = DrinkList(
            hass,
            config_entry.entry_id,
            store,
            aiohttp_client.async_get_clientsession(hass),
        )
        # Follows the machine's status, settings and capabilities through the
        # cloud, live, whenever the internet is available.
        self._cloud = DaedalusCloudConnection(
            aiohttp_client.async_get_clientsession(hass),
            appliance.machine_name,
            cloud_client.async_get_token,
            self._handle_shadow,
            on_connected=self._cloud_connected,
            # The machine's answers and brew reports, also for drinks made in the app.
            on_machine_message=self._handle_machine_message,
        )

        self._local_ws: DaedalusLocalWebSocket | None = None
        self._unregister_ws_cb: Any | None = None

        # Talk to the machine directly only once the person has confirmed it:
        # the token never goes to a device that wasn't confirmed.
        fingerprint = store.machine_fingerprint if store else None
        if appliance.local_ip and fingerprint:
            self._init_local_websocket(appliance.local_ip, fingerprint)
        self._set_issue("machine_not_confirmed", fingerprint is None)

    def _init_local_websocket(self, host: str, fingerprint: bytes) -> None:
        """Connect to the confirmed machine over the home network."""
        self._local_ws = DaedalusLocalWebSocket(
            host=host,
            serial_number=self.appliance.serial_number,
            token_provider=self.cloud_client.async_get_token,
            fingerprint=fingerprint,
            on_identity_changed=self._identity_changed,
            on_token_rejected=lambda rejected: self._set_issue("machine_refuses_account", rejected),
        )
        self._unregister_ws_cb = self._local_ws.register_callback(self._handle_machine_message)

    def _identity_changed(self, mismatch: bool) -> None:
        """Show (or clear) a repair notice when the machine's certificate changes."""
        self._set_issue("machine_identity_changed", mismatch)

    def _set_issue(self, key: str, active: bool) -> None:
        """Show or clear one of this machine's repair notices."""
        issue_id = f"{key}_{self.appliance.serial_number}"
        if not active:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR if key != "machine_not_confirmed" else ir.IssueSeverity.WARNING,
            translation_key=key,
            translation_placeholders={"name": self.appliance.display_name},
        )

    async def async_start(self) -> None:
        """Start local WebSocket listener and initial coordinator refresh."""
        # Check the cloud sign-in first: if it fails, setup stops before any
        # local connection is opened, so nothing is left running.
        await self.drinks.async_load_photos()
        await self.async_config_entry_first_refresh()
        if self._local_ws:
            await self._local_ws.async_start()
        await self._cloud.async_start()

    async def async_stop(self) -> None:
        """Stop local WebSocket and release network resources."""
        await self._cloud.async_stop()
        await self.drinks.async_stop()
        if self.store:
            # Don't leave a change (a new token, a filter date) in the delayed write.
            await self.store.async_save_now()
        if self._unregister_ws_cb:
            self._unregister_ws_cb()
            self._unregister_ws_cb = None
        if self._local_ws:
            await self._local_ws.async_stop()
            self._local_ws = None
        await self.cloud_client.async_close()

    def _handle_machine_message(self, data: dict[str, Any]) -> None:
        """A message from the machine, over the home network or relayed by the cloud."""
        msg_type = data.get("Message")
        if isinstance(data.get("ProfileId"), int) and data.get("Response") == RESPONSE_OK:
            self._saw_profile(data["ProfileId"])

        # 0. The machine's own status report: the source of truth when it comes.
        if (status := MachineStatus.from_message(data)) is not None:
            self.state.update_from_status(status)
            self.async_set_updated_data(self.state)

        # 1. Power / Standby transition report (only an OK means it happened)
        elif msg_type == RESPONSE_MACHINE_UI:
            turn_on_off = data.get("TurnOnOff")
            if isinstance(turn_on_off, int) and data.get("Response") == RESPONSE_OK:
                self.state.update_from_ui_response(turn_on_off)
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
            self.drinks.record_report(report.profile_id, report.recipe_id, data)
            self.state.operational_state = STATE_READY
            _LOGGER.info("Drink made: %s (number %s)", self.drinks.name(report.recipe_id), report.recipe_id)
            self.async_set_updated_data(self.state)

        # 3. Settings configuration response
        elif msg_type == RESPONSE_SET_SETTINGS:
            _LOGGER.debug("Appliance settings successfully acknowledged")

        # Update local connectivity flag
        self.state.is_local_connected = self._local_ws.is_connected if self._local_ws else False

    async def _async_update_data(self) -> MachineState:
        """Fetch latest status from cloud and verify local socket."""
        try:
            await self.cloud_client.async_renew_if_due()
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
            # Includes a locked account: ask the person instead of retrying.
            raise ConfigEntryAuthFailed(f"De'Longhi no longer accepts the saved sign-in: {err}") from err
        except (DaedalusConnectionError, DaedalusApiError) as err:
            _LOGGER.debug("Cloud poll failed (continuing with local state): %s", err)
        else:
            self._maybe_refresh_drinks()

        if self._local_ws:
            self.state.is_local_connected = self._local_ws.is_connected
        self._update_reachability()
        self._maybe_search_for_machine()
        return self.state

    @callback
    def _handle_shadow(self, name: str, reported: dict[str, Any]) -> None:
        """A cloud record was read or changed."""
        if name == "MachineStatus" and (status := MachineStatus.from_report(reported)):
            self._ip_hint = status.reported_ip
            self.state.update_from_status(status)
        elif name == "MachineSettings":
            self.state.settings = MachineSettings.from_report(reported)
            if self.state.settings:
                self._watch_filter(self.state.settings)
            if self.state.settings and self.state.settings.active_profile:
                self._saw_profile(self.state.settings.active_profile)
        elif name == "MachineCapabilities" and (caps := MachineCapabilities.from_report(reported)):
            self.state.capabilities = caps
            if self.store:
                self.store.set_capabilities(reported)
            self._update_device_registry()
            self._maybe_refresh_drinks()
        else:
            return
        self.async_set_updated_data(self.state)

    @property
    def machine_identity(self) -> MachineIdentity:
        """Model, SKU and firmware, as De'Longhi's content server asks for them."""
        caps = self.state.capabilities
        return MachineIdentity(
            model=self.appliance.model,
            sku=(caps.sku if caps and caps.sku else self.appliance.sku),
            firmware_ui=(caps.firmware_ui if caps else None) or "",
            firmware_wifi=(caps.firmware_wifi if caps else None) or "",
            firmware_mainboard=(caps.firmware_mainboard if caps else None) or "",
        )

    @callback
    def _maybe_refresh_drinks(self) -> None:
        self.drinks.maybe_refresh(
            self.machine_identity,
            self.cloud_client.async_get_token,
            self.hass.config.language,
            self.hass.config.country,
        )

    @callback
    def _cloud_connected(self, connected: bool) -> None:
        self.state.is_cloud_connected = connected
        self.async_set_updated_data(self.state)

    @property
    def device_info(self) -> DeviceInfo:
        """The machine as a Home Assistant device: the one place this is defined."""
        caps = self.state.capabilities
        info = DeviceInfo(
            identifiers={(DOMAIN, self.appliance.serial_number)},
            name=self.appliance.display_name,
            manufacturer=MANUFACTURER,
            model=self.appliance.model or None,
            serial_number=self.appliance.serial_number,
        )
        if caps:
            if caps.sku:
                info["model_id"] = caps.sku
            if caps.firmware_ui:
                info["sw_version"] = caps.firmware_ui
            if caps.firmware_wifi or caps.firmware_mainboard:
                info["hw_version"] = ", ".join(
                    part
                    for part in (
                        caps.firmware_mainboard and f"mainboard {caps.firmware_mainboard}",
                        caps.firmware_wifi and f"Wi-Fi {caps.firmware_wifi}",
                    )
                    if part
                )
            if caps.mac:
                info["connections"] = {(dr.CONNECTION_NETWORK_MAC, dr.format_mac(caps.mac))}
        return info

    @callback
    def _update_device_registry(self) -> None:
        """Bring the device page up to date when the capabilities change."""
        if self.config_entry is None:
            return
        info = self.device_info
        dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.config_entry.entry_id, **info
        )

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
        """Re-find the confirmed machine after it moved, by its certificate only.

        No token is sent while searching. A machine that was never confirmed
        isn't searched for: the person confirms it in the options instead.
        """
        fingerprint = self.store.machine_fingerprint if self.store else None
        if fingerprint is None or self.config_entry is None:
            return
        found = None
        if self._ip_hint and self._ip_hint != self.appliance.local_ip:
            # The address the machine reported to the cloud first.
            found = await async_find_machine([self._ip_hint], fingerprint)
        found = found or await async_find_local_machine(self.hass, fingerprint)
        if found is None or found == self.appliance.local_ip:
            return
        _LOGGER.info("Found the coffee machine at a new address")
        # Saving the address reloads the integration, which connects there.
        self.hass.config_entries.async_update_entry(
            self.config_entry, options={**self.config_entry.options, CONF_DEVICE_IP: found}
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

    @property
    def filter_changed(self) -> datetime | None:
        """When a new water filter was last registered on the machine, if seen."""
        changed = self.store.filter.get("changed") if self.store else None
        return dt_util.parse_datetime(changed) if changed else None

    def _watch_filter(self, settings: MachineSettings) -> None:
        """Record the date when a new water filter is registered on the machine.

        The machine counts filters (FilterTotCnt) and resets the filter-used
        percentage for a new one. The first reading is only a baseline: a
        change is recorded only when one is actually seen happening.
        """
        if self.store is None or settings.filters_installed is None:
            return
        last = self.store.filter
        count, usage = settings.filters_installed, settings.filter_usage
        changed = last.get("changed")
        if "count" in last:
            counted = count > last["count"]
            reset = (
                usage is not None
                and last.get("usage") is not None
                and usage <= FILTER_RESET_BELOW
                and last["usage"] - usage >= FILTER_RESET_DROP
            )
            if counted or reset:
                changed = dt_util.utcnow().isoformat()
                _LOGGER.info("New water filter registered on the coffee machine")
        self.store.set_filter({"count": count, "usage": usage, "changed": changed})

    def _saw_profile(self, profile: int) -> None:
        if profile > 0 and profile not in self.known_profiles:
            self.known_profiles.add(profile)
            if self.store:
                self.store.set_profiles(sorted(self.known_profiles))

    @property
    def can_brew(self) -> bool:
        """The one rule for whether a drink may start: every brew control uses it.

        Only when the machine is known to be on and ready, isn't busy, has no
        alarm that would make a drink go wrong, and is reachable directly.
        """
        state = self.state
        if not (self._local_ws and self._local_ws.is_connected) or state.is_on is not True:
            return False
        status = state.status
        if status is None or status.status == STATUS_BUSY:
            return False
        return not any(status.alarms.get(alarm) for alarm in BREW_BLOCKING_ALARMS)

    @property
    def machine_profile(self) -> int | None:
        """The profile active on the machine itself, if known."""
        settings = self.state.settings
        return settings.active_profile if settings and settings.active_profile else None

    @property
    def active_profile(self) -> int:
        """The profile drinks brew with: chosen in Home Assistant, else the machine's.

        The app works the same way: its profile toggle never talks to the
        machine; the profile travels with each drink command (seen 2026-11-04).
        """
        return self.brew_profile or self.machine_profile or 1

    async def async_brew(self, recipe_id: int, profile_id: int | None = None) -> None:
        """Make a drink: its values as the machine makes it for this profile, with
        any changes saved in Home Assistant (see recipes.py)."""
        profile = profile_id or self.active_profile
        values = self.drinks.recipe(recipe_id, profile)
        await self._async_command(lambda ws: ws.async_brew(recipe_id, profile, values))
        self.state.operational_state = STATE_BREWING
        self.async_set_updated_data(self.state)

    async def async_set_setting(self, field_name: str, value: int) -> None:
        """Change one top-level setting: CoffeeTemp, AutoTurnOffTimeInMin or WaterHardness."""
        await self._async_command(lambda ws: ws.async_set_settings({field_name: value}))
        if self.state.settings is not None:
            attr = SETTING_FIELDS[field_name]
            self.state.settings = dataclasses.replace(self.state.settings, **{attr: value})
        self.async_set_updated_data(self.state)

    async def async_set_switch(self, name: str, on: bool) -> None:
        """Turn one of the machine's switches (in UserConf, e.g. SoundEnable) on or off.

        Sends the whole UserConf group with only this switch changed, so the
        other switches are certain to keep their values.
        """
        if self.state.settings is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="settings_not_known"
            )
        current = dict(self.state.settings.switches)
        current[name] = on
        await self._async_command(lambda ws: ws.async_set_settings({"UserConf": current}))
        if self.state.settings is not None:
            self.state.settings = dataclasses.replace(self.state.settings, switches=current)
        self.async_set_updated_data(self.state)
