"""Local WebSocket client for De'Longhi Daedalus LAN2LAN protocol (/ws/lan2lan)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import random
import ssl
import string
from typing import Any

import aiohttp

from ..const import (
    APP_ID_MCL,
    DEFAULT_RETRY_DELAY,
    DEFAULT_TIMEOUT,
    DEFAULT_WS_PATH,
    DEFAULT_WS_PORT,
    MAX_RECONNECT_DELAY,
    MESSAGE_AUTH,
    MESSAGE_BEVERAGE_REQUEST,
    MESSAGE_MACHINE_UI,
    MESSAGE_SET_MACHINE_SETTINGS,
    RESPONSE_BEVERAGE_REPORT,
    RESPONSE_BEVERAGE_REQUEST,
    RESPONSE_MACHINE_UI,
    RESPONSE_OK,
    RESPONSE_SET_SETTINGS,
    TURN_ON_OFF_STANDBY,
    TURN_ON_OFF_WAKE,
)
from .models import BeverageParameters, BeverageReport, MachineSettings

_LOGGER = logging.getLogger(__name__)


def generate_request_id(length: int = 5) -> str:
    """Generate alphanumeric request nonce matching official app."""
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


class DaedalusLocalWebSocket:
    """Manages secure, low-latency WebSocket connection to the coffee machine on LAN."""

    def __init__(
        self,
        host: str,
        serial_number: str,
        auth_token: str | None = None,
        port: int = DEFAULT_WS_PORT,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        """Initialize local WebSocket manager."""
        self._host = host
        self._port = port
        self._serial_number = serial_number
        self._auth_token = auth_token or ""
        self._session = session
        self._owns_session = False
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._connection_id: int | None = None
        self._is_connected = False
        self._should_run = False
        self._loop_task: asyncio.Task[None] | None = None
        self._callbacks: list[Callable[[dict[str, Any]], None]] = []

    @property
    def is_connected(self) -> bool:
        """Return True if authenticated WebSocket session is active."""
        return self._is_connected and self._ws is not None and not self._ws.closed

    @property
    def host(self) -> str:
        """Return current IP address or hostname of appliance."""
        return self._host

    def update_host(self, new_host: str) -> None:
        """Update host IP address if updated via DHCP / cloud shadow."""
        if self._host != new_host:
            _LOGGER.info("Updating Daedalus appliance IP address from %s to %s", self._host, new_host)
            self._host = new_host

    def register_callback(self, callback: Callable[[dict[str, Any]], None]) -> Callable[[], None]:
        """Register a callback for incoming messages and return unregister hook."""
        self._callbacks.append(callback)

        def unregister() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unregister

    def _notify_callbacks(self, message: dict[str, Any]) -> None:
        """Deliver message payload to registered listeners."""
        for cb in list(self._callbacks):
            try:
                cb(message)
            except Exception as err:
                _LOGGER.exception("Error executing Daedalus callback: %s", err)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Retrieve or create active aiohttp ClientSession."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    def _create_ssl_context(self) -> ssl.SSLContext:
        """Create trust-all SSL context matching ESP32 self-signed certificate."""
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def async_start(self) -> None:
        """Start background connection maintenance loop."""
        if self._should_run:
            return
        self._should_run = True
        self._loop_task = asyncio.create_task(self._maintain_connection())

    async def async_stop(self) -> None:
        """Stop background connection loop and cleanly disconnect."""
        self._should_run = False
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        await self._disconnect()
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def _disconnect(self) -> None:
        """Close active WebSocket connection."""
        self._is_connected = False
        self._connection_id = None
        if self._ws and not self._ws.closed:
            await self._ws.close()
        self._ws = None

    async def _maintain_connection(self) -> None:
        """Maintain persistent connection with exponential backoff on drop."""
        delay = DEFAULT_RETRY_DELAY
        while self._should_run:
            try:
                await self._connect_and_listen()
                delay = DEFAULT_RETRY_DELAY
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.debug("Daedalus local WebSocket connection error (%s): %s", self._host, err)
                await self._disconnect()
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_RECONNECT_DELAY)

    async def _connect_and_listen(self) -> None:
        """Connect to appliance WebSocket, authenticate, and process messages."""
        session = await self._get_session()
        ssl_ctx = self._create_ssl_context()
        url = f"wss://{self._host}:{self._port}{DEFAULT_WS_PATH}"

        _LOGGER.debug("Connecting to Daedalus local WebSocket at %s", url)
        async with session.ws_connect(
            url,
            ssl=ssl_ctx,
            timeout=DEFAULT_TIMEOUT,
            heartbeat=30.0,
        ) as ws:
            self._ws = ws
            # Step 1: Send authentication handshake
            auth_payload = {
                "Message": MESSAGE_AUTH,
                "SerialNo": self._serial_number,
                "AuthToken": self._auth_token,
            }
            await ws.send_json(auth_payload)
            _LOGGER.debug("Sent auth handshake for serial %s", self._serial_number)

            # Step 2: Message listening loop
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        await self._handle_incoming_message(data)
                    except json.JSONDecodeError:
                        _LOGGER.warning("Received invalid non-JSON payload from appliance: %s", msg.data)
                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    _LOGGER.debug("WebSocket connection closed or error received")
                    break

    async def _handle_incoming_message(self, data: dict[str, Any]) -> None:
        """Parse and route messages from the appliance."""
        _LOGGER.debug("Received Daedalus message: %s", data)

        # Check for authentication response
        if "ConnectionId" in data and not self._is_connected:
            self._connection_id = data["ConnectionId"]
            self._is_connected = True
            _LOGGER.info(
                "Successfully authenticated with Daedalus appliance on LAN (ConnectionId: %s)",
                self._connection_id,
            )

        self._notify_callbacks(data)

    async def async_send_command(self, payload: dict[str, Any]) -> bool:
        """Send an arbitrary JSON command over the authenticated socket."""
        if not self.is_connected or self._ws is None:
            _LOGGER.warning("Cannot send command: local WebSocket is not connected to %s", self._host)
            return False

        if "ConnectionId" not in payload and self._connection_id is not None:
            payload["ConnectionId"] = self._connection_id

        try:
            await self._ws.send_json(payload)
            _LOGGER.debug("Dispatched LAN command: %s", payload)
            return True
        except Exception as err:
            _LOGGER.error("Failed to send Daedalus command over WebSocket: %s", err)
            return False

    async def async_turn_on(self) -> bool:
        """Wake machine from standby mode."""
        payload = {
            "Message": MESSAGE_MACHINE_UI,
            "AppId": APP_ID_MCL,
            "RequestId": generate_request_id(),
            "TurnOnOff": TURN_ON_OFF_WAKE,
        }
        return await self.async_send_command(payload)

    async def async_turn_off(self) -> bool:
        """Put machine into standby / sleep mode."""
        payload = {
            "Message": MESSAGE_MACHINE_UI,
            "AppId": APP_ID_MCL,
            "RequestId": generate_request_id(),
            "TurnOnOff": TURN_ON_OFF_STANDBY,
        }
        return await self.async_send_command(payload)

    async def async_brew(
        self,
        recipe_id: int,
        profile_id: int = 1,
        parameters: BeverageParameters | None = None,
    ) -> bool:
        """Dispatch drink brewing request with customized parameters."""
        payload: dict[str, Any] = {
            "Message": MESSAGE_BEVERAGE_REQUEST,
            "AppId": APP_ID_MCL,
            "RequestId": generate_request_id(),
            "RecipeId": recipe_id,
            "ProfileId": profile_id,
        }
        if parameters:
            payload["Parameters"] = parameters.to_dict()

        return await self.async_send_command(payload)

    async def async_set_settings(self, settings: MachineSettings) -> bool:
        """Configure machine settings (temperature, auto-off, water hardness)."""
        payload = {
            "Message": MESSAGE_SET_MACHINE_SETTINGS,
            "AppId": APP_ID_MCL,
            "RequestId": generate_request_id(),
            "Settings": settings.to_dict(),
        }
        return await self.async_send_command(payload)
