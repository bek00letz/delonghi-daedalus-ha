"""A lasting connection to De'Longhi's cloud (AWS IoT) for one coffee machine.

The machine keeps three "named shadows" in AWS IoT: MachineStatus (what it's
doing, alarms), MachineSettings (settings and counters) and
MachineCapabilities (model, firmware, which options exist). This connection
reads all three when it connects and then receives every change live, so
Home Assistant knows the machine's state even when it can't reach it on the
home network, or when it's used from the phone app.

It speaks just enough MQTT 5 over a secure WebSocket, the way the My Coffee
Lounge app does (seen in the app's code, 2026-11-04):

    wss://<endpoint>/mqtt?x-amz-customauthorizer-name=<authorizer>
    username "x-amz-customauthorizer-name=<authorizer>", password = account token
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
import logging
import struct
from typing import Any
import uuid

import aiohttp

from ..const import AWS_CUSTOM_AUTHORIZER, AWS_IOT_ATS_HOST

_LOGGER = logging.getLogger(__name__)

SHADOWS = ("MachineStatus", "MachineSettings", "MachineCapabilities")
KEEPALIVE = 60  # seconds, announced to the server
PING_EVERY = 25  # seconds
CONNECT_TIMEOUT = 15.0
RETRY_DELAY = 10.0
MAX_RETRY_DELAY = 300.0

CONNECT, CONNACK, PUBLISH, PUBACK = 0x10, 0x20, 0x30, 0x40
SUBSCRIBE, SUBACK, PINGREQ, DISCONNECT = 0x82, 0x90, 0xC0, 0xE0
# CONNACK reasons that mean the token was refused.
AUTH_REFUSED = {0x86, 0x87, 0x8C}

TokenProvider = Callable[[bool], Awaitable[str]]
ShadowCallback = Callable[[str, dict[str, Any]], None]


class CloudError(Exception):
    """The cloud couldn't be reached or refused the request."""


class CloudAuthRefused(CloudError):
    """The cloud refused the account token."""


# --- MQTT 5 packets ---------------------------------------------------------


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte, value = value % 128, value // 128
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        byte = data[pos]
        value |= (byte & 0x7F) << shift
        pos += 1
        if not byte & 0x80:
            return value, pos
        shift += 7


def _string(value: str | bytes) -> bytes:
    raw = value.encode() if isinstance(value, str) else value
    return struct.pack("!H", len(raw)) + raw


def _packet(kind: int, body: bytes) -> bytes:
    return bytes([kind]) + _varint(len(body)) + body


def connect_packet(client_id: str, username: str, password: str) -> bytes:
    """MQTT 5 CONNECT with clean start, username and password."""
    body = (
        _string("MQTT")
        + bytes([5, 0x80 | 0x40 | 0x02])
        + struct.pack("!H", KEEPALIVE)
        + _varint(0)
        + _string(client_id)
        + _string(username)
        + _string(password)
    )
    return _packet(CONNECT, body)


def subscribe_packet(packet_id: int, topics: list[str]) -> bytes:
    body = struct.pack("!H", packet_id) + _varint(0)
    for topic in topics:
        body += _string(topic) + b"\x01"  # QoS 1
    return _packet(SUBSCRIBE, body)


def publish_packet(topic: str, payload: bytes) -> bytes:
    """QoS 0 PUBLISH, so no acknowledgement is involved."""
    return _packet(PUBLISH, _string(topic) + _varint(0) + payload)


def parse_publish(data: bytes) -> tuple[str, bytes, bytes | None]:
    """Topic, payload and (for QoS 1/2) packet id of an incoming PUBLISH."""
    flags = data[0]
    _, pos = _read_varint(data, 1)
    (topic_len,) = struct.unpack_from("!H", data, pos)
    topic = data[pos + 2 : pos + 2 + topic_len].decode()
    pos += 2 + topic_len
    packet_id = None
    if (flags >> 1) & 0x03:
        packet_id = data[pos : pos + 2]
        pos += 2
    props_len, pos = _read_varint(data, pos)
    return topic, data[pos + props_len :], packet_id


def merge(base: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial shadow update (only changed fields) to a full copy."""
    out = dict(base)
    for key, value in change.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        elif value is None:
            out.pop(key, None)  # AWS sends null for a removed field
        else:
            out[key] = value
    return out


# --- the connection -----------------------------------------------------------


class DaedalusCloudConnection:
    """Keeps one MQTT connection to AWS IoT for a machine and follows its shadows."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        thing_name: str,
        token_provider: TokenProvider,
        on_shadow: ShadowCallback,
        *,
        on_connected: Callable[[bool], None] | None = None,
        on_machine_message: Callable[[dict[str, Any]], None] | None = None,
        host: str = AWS_IOT_ATS_HOST,
        authorizer: str = AWS_CUSTOM_AUTHORIZER,
        ssl: bool | None = None,
    ) -> None:
        """Initialize the connection.

        ``on_shadow(name, reported)`` is called with the full ``reported``
        state each time a shadow is read or changes.
        """
        self._session = session
        self._thing = thing_name
        self._token_provider = token_provider
        self._on_shadow = on_shadow
        self._on_connected = on_connected
        self._on_machine_message = on_machine_message
        self._host = host
        self._authorizer = authorizer
        self._ssl = ssl
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task[None] | None = None
        self._reported: dict[str, dict[str, Any]] = {}
        self.is_connected = False

    def _shadow_topic(self, name: str, suffix: str) -> str:
        return f"$aws/things/{self._thing}/shadow/name/{name}/{suffix}"

    @property
    def responses_topic(self) -> str:
        """The machine's answers and brew reports, whoever sent the command."""
        return f"{self._thing}/commands/response"

    def topics_for(self, shadow: str) -> list[str]:
        """What this connection listens to for one shadow."""
        return [
            self._shadow_topic(shadow, suffix)
            for suffix in ("get/accepted", "get/rejected", "update/accepted")
        ]

    async def async_start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="delonghi_daedalus cloud")

    async def async_stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._set_connected(False)

    async def async_request_shadows(self) -> None:
        """Ask for a fresh copy of every shadow; answers arrive via ``on_shadow``."""
        for name in SHADOWS:
            await self._send(publish_packet(self._shadow_topic(name, "get"), b"{}"))

    async def _send(self, packet: bytes) -> None:
        if self._ws is None or self._ws.closed:
            raise CloudError("not connected")
        await self._ws.send_bytes(packet)

    def _set_connected(self, connected: bool) -> None:
        if connected != self.is_connected:
            self.is_connected = connected
            if self._on_connected is not None:
                self._on_connected(connected)

    async def _run(self) -> None:
        delay = RETRY_DELAY
        refresh = False
        while True:
            try:
                await self._session_once(refresh)
                delay = RETRY_DELAY  # ran and ended normally: reconnect soon
                refresh = False
            except asyncio.CancelledError:
                raise
            except CloudAuthRefused:
                # Most likely an expired token: one immediate retry with a fresh one.
                if not refresh:
                    refresh = True
                    continue
                _LOGGER.warning("De'Longhi's cloud refused the account token")
                refresh = False
            except Exception as err:  # keep trying through network trouble
                _LOGGER.debug("Cloud connection dropped: %s", err)
            finally:
                self._set_connected(False)
            await asyncio.sleep(delay)
            delay = min(delay * 2, MAX_RETRY_DELAY)

    async def _session_once(self, refresh_token: bool) -> None:
        token = await self._token_provider(refresh_token)
        auth = f"x-amz-customauthorizer-name={self._authorizer}"
        async with asyncio.timeout(CONNECT_TIMEOUT):
            ws = await self._session.ws_connect(
                f"wss://{self._host}/mqtt?{auth}", protocols=("mqtt",), ssl=self._ssl
            )
        self._ws = ws
        pinger: asyncio.Task[None] | None = None
        try:
            await ws.send_bytes(connect_packet(f"dlg-ha-{uuid.uuid4().hex}", auth, token))
            async with asyncio.timeout(CONNECT_TIMEOUT):
                connack = await self._receive(ws)
            if connack[0] & 0xF0 != CONNACK:
                raise CloudError("no CONNACK")
            if connack[3] in AUTH_REFUSED:
                raise CloudAuthRefused(f"reason 0x{connack[3]:02x}")
            if connack[3] != 0:
                raise CloudError(f"connection refused (reason 0x{connack[3]:02x})")

            # AWS IoT refuses a whole SUBSCRIBE with more than 8 topics, so
            # subscribe one shadow (3 topics) at a time, and wait for each to be
            # in place: otherwise answers to the requests below find no listener.
            groups = [self.topics_for(name) for name in SHADOWS] + [[self.responses_topic]]
            for packet_id, topics in enumerate(groups, start=1):
                await ws.send_bytes(subscribe_packet(packet_id, topics))
                async with asyncio.timeout(CONNECT_TIMEOUT):
                    while (suback := await self._receive(ws))[0] & 0xF0 != SUBACK:
                        pass
                refused = [t for t, code in zip(topics, suback[-len(topics) :]) if code >= 0x80]
                if refused:
                    _LOGGER.debug("Cloud refused subscriptions: %s", refused)
            self._set_connected(True)
            await self.async_request_shadows()
            pinger = asyncio.create_task(self._ping(ws))
            while True:
                data = await self._receive(ws)
                kind = data[0] & 0xF0
                if kind == PUBLISH:
                    topic, payload, packet_id = parse_publish(data)
                    if packet_id is not None:
                        await ws.send_bytes(bytes([PUBACK, 2]) + packet_id)
                    self._handle(topic, payload)
                elif kind == DISCONNECT:
                    raise CloudError("server closed the session")
        finally:
            if pinger is not None:
                pinger.cancel()
            if not ws.closed:
                await ws.close()

    @staticmethod
    async def _receive(ws: aiohttp.ClientWebSocketResponse) -> bytes:
        msg = await ws.receive()
        if msg.type != aiohttp.WSMsgType.BINARY:
            raise CloudError(f"connection ended ({msg.type.name})")
        return msg.data

    @staticmethod
    async def _ping(ws: aiohttp.ClientWebSocketResponse) -> None:
        while not ws.closed:
            await asyncio.sleep(PING_EVERY)
            await ws.send_bytes(bytes([PINGREQ, 0]))

    def _handle(self, topic: str, payload: bytes) -> None:
        if topic == self.responses_topic:
            try:
                message = json.loads(payload)
            except ValueError:
                return
            if isinstance(message, dict) and self._on_machine_message is not None:
                self._on_machine_message(message)
            return
        prefix = f"$aws/things/{self._thing}/shadow/name/"
        if not topic.startswith(prefix):
            return
        name, _, suffix = topic[len(prefix) :].partition("/")
        if suffix == "get/rejected":
            _LOGGER.debug("Cloud has no %s for this machine: %s", name, payload[:200])
            return
        try:
            reported = json.loads(payload)["state"]["reported"]
        except (ValueError, KeyError, TypeError):
            return
        if not isinstance(reported, dict):
            return
        if suffix == "update/accepted":
            reported = merge(self._reported.get(name, {}), reported)
        self._reported[name] = reported
        self._on_shadow(name, reported)
