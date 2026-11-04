"""Tests for the lasting cloud connection (AWS IoT over MQTT 5), against a fake broker."""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any

from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.delonghi_daedalus.api.cloud import (
    SHADOWS,
    DaedalusCloudConnection,
    _read_varint,
    _varint,
    connect_packet,
    merge,
    parse_publish,
)
from tests.fake_machine import make_tls

pytestmark = pytest.mark.usefixtures("socket_enabled")

THING = "THING0001"
TOKEN = "account-token"
AUTH = "dlg-prod-token-authorizer"

# Shapes as the real shadows held them on 2026-11-04 (no personal data).
SHADOW_DATA: dict[str, dict[str, Any]] = {
    "MachineStatus": {"Status": "Idle", "CurrMode": 7, "alarms": {"Filter": True}},
    "MachineSettings": {"Editable": {"CoffeeTemp": 1, "SetProfile": 2}, "NotEditable": {"DecalPerc": 49}},
    "MachineCapabilities": {"SKU": "0132250187", "FWUIVersion": "1.1.15"},
}


def _string(data: bytes, pos: int) -> tuple[str, int]:
    (length,) = struct.unpack_from("!H", data, pos)
    return data[pos + 2 : pos + 2 + length].decode(), pos + 2 + length


def _publish(topic: str, payload: dict[str, Any]) -> bytes:
    body = struct.pack("!H", len(topic)) + topic.encode() + b"\x00" + json.dumps(payload).encode()
    return bytes([0x30]) + _varint(len(body)) + body


class FakeIot:
    """Answers like AWS IoT: refuses SUBSCRIBEs over 8 topics, serves shadows, pushes updates."""

    def __init__(self, *, good_tokens: set[str] | None = None) -> None:
        self.good_tokens = good_tokens or {TOKEN}
        self.passwords: list[str] = []
        self.subscribed: list[str] = []
        self.sockets: list[web.WebSocketResponse] = []
        self.ready = asyncio.Event()

    async def push(self, topic: str, payload: dict[str, Any]) -> None:
        for ws in self.sockets:
            await ws.send_bytes(_publish(topic, payload))

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        assert dict(request.query) == {"x-amz-customauthorizer-name": AUTH}
        ws = web.WebSocketResponse(protocols=("mqtt",))
        await ws.prepare(request)
        self.sockets.append(ws)
        async for msg in ws:
            if msg.type != WSMsgType.BINARY:
                continue
            data = msg.data
            kind = data[0] & 0xF0
            if kind == 0x10:  # CONNECT
                _, pos = _read_varint(data, 1)
                pos += 6 + 1 + 1 + 2
                props, pos = _read_varint(data, pos)
                pos += props
                _, pos = _string(data, pos)  # client id
                _, pos = _string(data, pos)  # username
                password, _ = _string(data, pos)
                self.passwords.append(password)
                ok = password in self.good_tokens
                await ws.send_bytes(bytes([0x20, 3, 0, 0x00 if ok else 0x87, 0]))
                if not ok:
                    await ws.close()
            elif kind == 0x80:  # SUBSCRIBE
                _, pos = _read_varint(data, 1)
                packet_id = data[pos : pos + 2]
                pos += 2
                props, pos = _read_varint(data, pos)
                pos += props
                topics = []
                while pos < len(data):
                    topic, pos = _string(data, pos)
                    pos += 1
                    topics.append(topic)
                codes = [0x97 if len(topics) > 8 else 0x01] * len(topics)
                if len(topics) <= 8:
                    self.subscribed += topics
                await ws.send_bytes(bytes([0x90, 3 + len(codes)]) + packet_id + b"\x00" + bytes(codes))
            elif kind == 0x30:  # PUBLISH to .../get
                topic, _ = _string(data, 2)
                base = topic.removesuffix("/get")
                name = base.rsplit("/", 1)[1]
                if f"{base}/get/accepted" in self.subscribed:
                    await ws.send_bytes(
                        _publish(f"{base}/get/accepted", {"state": {"reported": SHADOW_DATA[name]}})
                    )
                if len({t.rsplit("/", 3)[0] for t in self.subscribed}) == len(SHADOWS):
                    self.ready.set()
        return ws


async def _connected(tmp_path, fake: FakeIot, *, expired_first: bool = False):
    ctx, _ = make_tls(tmp_path, "*.iot.eu-central-1.amazonaws.com")
    app = web.Application()
    app.router.add_get("/mqtt", fake.handler)
    server = TestServer(app)
    await server.start_server(ssl=ctx)
    session = ClientSession()
    got: dict[str, dict[str, Any]] = {}
    tokens: list[bool] = []

    async def token(refresh: bool) -> str:
        tokens.append(refresh)
        return "expired" if expired_first and not refresh else TOKEN

    cloud = DaedalusCloudConnection(
        session,
        THING,
        token,
        lambda name, reported: got.__setitem__(name, reported),
        host=f"127.0.0.1:{server.port}",
        authorizer=AUTH,
        ssl=False,
    )
    await cloud.async_start()
    return server, session, cloud, got, tokens


async def _wait(condition, timeout: float = 5) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def test_reads_every_shadow_and_follows_changes(tmp_path) -> None:
    fake = FakeIot()
    server, session, cloud, got, _ = await _connected(tmp_path, fake)
    try:
        await _wait(lambda: len(got) == 3)
        assert got == SHADOW_DATA
        assert cloud.is_connected
        assert fake.passwords == [TOKEN]

        # AWS sends only what changed; the full copy is kept up to date.
        base = f"$aws/things/{THING}/shadow/name/MachineStatus"
        await fake.push(f"{base}/update/accepted", {"state": {"reported": {"Status": "Standby"}}})
        await _wait(lambda: got["MachineStatus"]["Status"] == "Standby")
        assert got["MachineStatus"]["alarms"] == {"Filter": True}
    finally:
        await cloud.async_stop()
        await session.close()
        await server.close()


async def test_subscribes_within_awss_limit(tmp_path) -> None:
    """AWS refuses a whole SUBSCRIBE over 8 topics; ours must all be accepted."""
    fake = FakeIot()
    server, session, cloud, got, _ = await _connected(tmp_path, fake)
    try:
        await _wait(lambda: len(got) == 3)
        # 3 topics per shadow, plus the machine's command responses.
        assert len(fake.subscribed) == 3 * len(SHADOWS) + 1
        assert f"{THING}/commands/response" in fake.subscribed
    finally:
        await cloud.async_stop()
        await session.close()
        await server.close()


async def test_refused_token_is_renewed_once(tmp_path) -> None:
    fake = FakeIot()
    server, session, cloud, got, tokens = await _connected(tmp_path, fake, expired_first=True)
    try:
        await _wait(lambda: len(got) == 3)
        assert tokens == [False, True]  # asked again, for a fresh token
        assert fake.passwords == ["expired", TOKEN]
    finally:
        await cloud.async_stop()
        await session.close()
        await server.close()


def test_merge_keeps_unchanged_fields_and_drops_removed_ones() -> None:
    base = {"a": 1, "nested": {"x": 1, "y": 2}, "gone": 5}
    assert merge(base, {"nested": {"y": 3}, "gone": None}) == {"a": 1, "nested": {"x": 1, "y": 3}}


def test_packet_helpers_round_trip() -> None:
    for n in (0, 127, 128, 16383, 16384, 2_000_000):
        assert _read_varint(_varint(n), 0) == (n, len(_varint(n)))
    assert connect_packet("id", "user", "pw")[0] == 0x10
    body = b"\x00\x05topic" + b"\x00" + b"payload"
    assert parse_publish(bytes([0x30]) + _varint(len(body)) + body) == ("topic", b"payload", None)
