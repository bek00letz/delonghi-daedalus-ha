"""Tests for reading the machine's stored status from the cloud (AWS IoT over MQTT 5)."""

from __future__ import annotations

import json
import struct
from typing import Any

from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.delonghi_daedalus.api.cloud_status import (
    CloudStatusError,
    _read_varint,
    _varint,
    async_fetch_status,
    connect_packet,
    parse_publish,
)
from custom_components.delonghi_daedalus.api.models import MachineStatus
from tests.fake_machine import make_tls

pytestmark = pytest.mark.usefixtures("socket_enabled")

THING = "THING0001"
TOKEN = "account-token"
AUTH = "dlg-prod-token-authorizer"

# The reported state as the real shadow held it on 2026-11-04 (no personal data).
REPORTED: dict[str, Any] = {
    "LanIpAddress": "192.0.2.8",
    "Status": "Idle",
    "CurrMode": 7,
    "alarms": {"TankLevel": False, "Filter": True, "NoCoffee": False},
    "peripherals": {"WaterSpout": True},
    "accessories": {"Water": True},
    "StepDescr": "Unknown",
}


def _string(data: bytes, pos: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("!H", data, pos)
    return data[pos + 2 : pos + 2 + length].decode(), pos + 2 + length


class FakeIot:
    """Answers like AWS IoT: CONNACK, SUBACK, then the shadow on /get."""

    def __init__(self, *, accept: bool = True, has_shadow: bool = True) -> None:
        self.accept = accept
        self.has_shadow = has_shadow
        self.connect: dict[str, str] = {}
        self.query: dict[str, str] = {}

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        self.query = dict(request.query)
        ws = web.WebSocketResponse(protocols=("mqtt",))
        await ws.prepare(request)
        async for msg in ws:
            if msg.type != WSMsgType.BINARY:
                continue
            data = msg.data
            kind = data[0] & 0xF0
            if kind == 0x10:  # CONNECT
                _, pos = _read_varint(data, 1)
                pos += 6 + 1 + 1 + 2  # "MQTT", version, flags, keepalive
                props, pos = _read_varint(data, pos)
                pos += props
                self.connect["client_id"], pos = _string(data, pos)
                self.connect["username"], pos = _string(data, pos)
                self.connect["password"], pos = _string(data, pos)
                ok = self.accept and self.connect["password"] == TOKEN
                await ws.send_bytes(bytes([0x20, 3, 0, 0x00 if ok else 0x87, 0]))
            elif kind == 0x80:  # SUBSCRIBE
                await ws.send_bytes(bytes([0x90, 5]) + data[2:4] + bytes([0, 1, 1]))
            elif kind == 0x30:  # PUBLISH to .../get
                topic, _ = _string(data, 2)
                base = topic.removesuffix("/get")
                if self.has_shadow:
                    reply_topic = f"{base}/get/accepted"
                    payload = json.dumps({"state": {"reported": REPORTED}, "version": 1}).encode()
                else:
                    reply_topic = f"{base}/get/rejected"
                    payload = b'{"code":404,"message":"No shadow exists"}'
                body = struct.pack("!H", len(reply_topic)) + reply_topic.encode() + b"\x00" + payload
                await ws.send_bytes(bytes([0x30]) + _varint(len(body)) + body)
        return ws


async def _fetch(fake: FakeIot, tmp_path) -> dict[str, Any]:
    ctx, _ = make_tls(tmp_path, "*.iot.eu-central-1.amazonaws.com")
    app = web.Application()
    app.router.add_get("/mqtt", fake.handler)
    server = TestServer(app)
    await server.start_server(ssl=ctx)
    try:
        async with ClientSession() as session:
            return await async_fetch_status(
                session,
                THING,
                TOKEN,
                host=f"127.0.0.1:{server.port}",
                authorizer=AUTH,
                ssl=False,  # the fake's certificate is self-signed
            )
    finally:
        await server.close()


async def test_reads_the_stored_status_like_the_app(tmp_path) -> None:
    fake = FakeIot()
    reported = await _fetch(fake, tmp_path)
    assert reported == REPORTED
    assert fake.query == {"x-amz-customauthorizer-name": AUTH}
    assert fake.connect["username"] == f"x-amz-customauthorizer-name={AUTH}"
    assert fake.connect["password"] == TOKEN
    assert fake.connect["client_id"].startswith("dlg-ha-")


async def test_refused_connection_is_an_error(tmp_path) -> None:
    with pytest.raises(CloudStatusError, match="refused"):
        await _fetch(FakeIot(accept=False), tmp_path)


async def test_no_stored_status_is_an_error(tmp_path) -> None:
    with pytest.raises(CloudStatusError, match="no stored status"):
        await _fetch(FakeIot(has_shadow=False), tmp_path)


def test_stored_status_parses_like_a_local_report() -> None:
    status = MachineStatus.from_report(REPORTED)
    assert status is not None
    assert status.display_status == "Ready"
    assert status.alarms["Filter"] is True
    assert status.reported_ip == "192.0.2.8"


def test_packet_helpers_round_trip() -> None:
    for n in (0, 127, 128, 16383, 16384, 2_000_000):
        assert _read_varint(_varint(n), 0) == (n, len(_varint(n)))
    packet = connect_packet("id", "user", "pw")
    assert packet[0] == 0x10
    body = b"\x00\x05topic" + b"\x00" + b"payload"
    assert parse_publish(bytes([0x30]) + _varint(len(body)) + body) == ("topic", b"payload")
