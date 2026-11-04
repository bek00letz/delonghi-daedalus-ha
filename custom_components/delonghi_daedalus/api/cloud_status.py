"""Read the machine's last reported status from De'Longhi's cloud (AWS IoT).

The machine reports its status to an AWS IoT "named shadow" called
MachineStatus whenever it changes. Reading it lets Home Assistant show the
real status right after a restart, before the machine reports locally.

This speaks just enough MQTT 5 (connect, subscribe, publish, receive) over a
secure WebSocket, the way the My Coffee Lounge app does (seen in the app's
code, 2026-11-04):

    wss://<endpoint>/mqtt?x-amz-customauthorizer-name=<authorizer>
    username "x-amz-customauthorizer-name=<authorizer>", password = account token
    subscribe  $aws/things/<thing>/shadow/name/MachineStatus/get/accepted
    publish    $aws/things/<thing>/shadow/name/MachineStatus/get
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
from typing import Any
import uuid

import aiohttp

from ..const import AWS_CUSTOM_AUTHORIZER, AWS_IOT_ATS_HOST, SHADOW_MACHINE_STATUS

_LOGGER = logging.getLogger(__name__)

FETCH_TIMEOUT = 15.0
KEEPALIVE = 30  # seconds; the connection lives only for one request

CONNECT, CONNACK, PUBLISH, SUBSCRIBE, SUBACK = 0x10, 0x20, 0x30, 0x82, 0x90


class CloudStatusError(Exception):
    """The stored status couldn't be read."""


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
    flags = 0x80 | 0x40 | 0x02
    body = (
        _string("MQTT")
        + bytes([5, flags])
        + struct.pack("!H", KEEPALIVE)
        + _varint(0)  # no properties
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


def parse_publish(data: bytes) -> tuple[str, bytes]:
    """Topic and payload of an incoming PUBLISH packet."""
    flags = data[0]
    _, pos = _read_varint(data, 1)
    (topic_len,) = struct.unpack_from("!H", data, pos)
    topic = data[pos + 2 : pos + 2 + topic_len].decode()
    pos += 2 + topic_len
    if (flags >> 1) & 0x03:  # QoS 1 or 2 carries a packet id
        pos += 2
    props_len, pos = _read_varint(data, pos)
    return topic, data[pos + props_len :]


async def async_fetch_status(
    session: aiohttp.ClientSession,
    thing_name: str,
    token: str,
    *,
    host: str = AWS_IOT_ATS_HOST,
    authorizer: str = AWS_CUSTOM_AUTHORIZER,
    ssl: bool | None = None,
) -> dict[str, Any]:
    """Return the ``reported`` part of the machine's MachineStatus shadow."""
    base = f"$aws/things/{thing_name}/shadow/name/{SHADOW_MACHINE_STATUS}"
    auth = f"x-amz-customauthorizer-name={authorizer}"
    try:
        async with asyncio.timeout(FETCH_TIMEOUT):
            async with session.ws_connect(
                f"wss://{host}/mqtt?{auth}", protocols=("mqtt",), ssl=ssl
            ) as ws:

                async def receive(kind: int) -> bytes:
                    while True:
                        msg = await ws.receive()
                        if msg.type != aiohttp.WSMsgType.BINARY:
                            raise CloudStatusError(f"connection ended ({msg.type.name})")
                        if msg.data[0] & 0xF0 == kind:
                            return msg.data

                await ws.send_bytes(connect_packet(f"dlg-ha-{uuid.uuid4().hex}", auth, token))
                connack = await receive(CONNACK)
                if connack[3] != 0:
                    raise CloudStatusError(f"connection refused (reason 0x{connack[3]:02x})")

                await ws.send_bytes(subscribe_packet(1, [f"{base}/get/accepted", f"{base}/get/rejected"]))
                await receive(SUBACK)
                await ws.send_bytes(publish_packet(f"{base}/get", b"{}"))

                topic, payload = parse_publish(await receive(PUBLISH))
    except (aiohttp.ClientError, OSError, TimeoutError) as err:
        raise CloudStatusError(f"couldn't reach the cloud: {err}") from err

    if topic.endswith("/get/rejected"):
        raise CloudStatusError(f"cloud has no stored status: {payload[:200]!r}")
    try:
        reported = json.loads(payload)["state"]["reported"]
    except (ValueError, KeyError, TypeError) as err:
        raise CloudStatusError("stored status couldn't be read") from err
    if not isinstance(reported, dict):
        raise CloudStatusError("stored status couldn't be read")
    return reported
