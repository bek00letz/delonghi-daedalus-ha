"""Tests for the local /ws/lan2lan connection, against a fake coffee machine.

The fake answers the way a real ECAM 470.85 did on 2026-11-04: the account's
De'Longhi token in AuthToken gets {"Response": "OK", "ConnectionId": n};
anything else gets {"Response": "KO"}.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import ssl
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
import pytest

from custom_components.delonghi_daedalus.api import websocket as websocket_module
from custom_components.delonghi_daedalus.api.websocket import (
    DaedalusCommandError,
    DaedalusLocalWebSocket,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")

SERIAL = "SN0001234567"
GOOD_TOKEN = "fresh-token"


@pytest.fixture(scope="module")
def machine_tls(tmp_path_factory) -> ssl.SSLContext:
    """A self-signed certificate, like the one the machine presents."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AWS IoT Certificate")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    folder = tmp_path_factory.mktemp("tls")
    (folder / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (folder / "key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(folder / "cert.pem", folder / "key.pem")
    return ctx


class FakeMachine:
    """Answers the auth handshake and records every message it receives."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.authed = asyncio.Event()
        self.command = asyncio.Event()
        # How to answer commands: "OK", "KO", or None to stay silent.
        self.reply: str | None = "OK"

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            data = msg.json()
            self.received.append(data)
            if data.get("Message") == "auth":
                ok = data.get("AuthToken") == GOOD_TOKEN and data.get("SerialNo") == SERIAL
                await ws.send_json(
                    {"Message": "auth", "Response": "OK", "ConnectionId": 7}
                    if ok
                    else {"Message": "auth", "Response": "KO"}
                )
                if ok:
                    self.authed.set()
            else:
                self.command.set()
                if "ConnectionId" not in data:
                    # What the real machine does: no RequestId in this answer.
                    await ws.send_json({"Message": data["Message"], "Response": "UNAUTHORIZED"})
                elif self.reply is not None:
                    await ws.send_json(
                        {
                            "Message": f"{data['Message']}Response",
                            "Response": self.reply,
                            "AppId": "MCL",
                            "RequestId": data["RequestId"],
                        }
                    )
        return ws


async def _connect(machine_tls, tokens: list[str]):
    """Start a fake machine and a local connection whose token provider hands out ``tokens``."""
    machine = FakeMachine()
    app = web.Application()
    app.router.add_get("/ws/lan2lan", machine.handler)
    server = TestServer(app)
    await server.start_server(ssl=machine_tls)

    calls: list[bool] = []

    async def provider(refresh: bool) -> str:
        calls.append(refresh)
        return tokens[min(len(calls), len(tokens)) - 1]

    local = DaedalusLocalWebSocket(
        host="127.0.0.1", serial_number=SERIAL, token_provider=provider, port=server.port
    )
    await local.async_start()
    return machine, server, local, calls


async def test_account_token_opens_local_session(machine_tls) -> None:
    machine, server, local, calls = await _connect(machine_tls, [GOOD_TOKEN])
    try:
        await asyncio.wait_for(machine.authed.wait(), 5)
        for _ in range(50):
            if local.is_connected:
                break
            await asyncio.sleep(0.01)
        assert local.is_connected
        assert calls == [False]

        reply = await local.async_turn_on()
        assert reply["Response"] == "OK"
        await asyncio.wait_for(machine.command.wait(), 5)
        command = machine.received[-1]
        assert command["Message"] == "MachineUI"
        assert command["ConnectionId"] == 7
    finally:
        await local.async_stop()
        await server.close()


# When the machine rejects a token, its close frame can arrive after aiohttp has
# closed the socket; aiohttp then re-arms its keep-alive timer, which fires once
# later and does nothing. Harmless, but Home Assistant's harness flags it.
_ALLOW_AIOHTTP_LATE_HEARTBEAT = pytest.mark.parametrize("expected_lingering_timers", [True])


@_ALLOW_AIOHTTP_LATE_HEARTBEAT
async def test_rejected_token_is_refreshed_once(machine_tls) -> None:
    """An expired token gets one immediate retry with a freshly fetched token."""
    machine, server, local, calls = await _connect(machine_tls, ["expired-token", GOOD_TOKEN])
    try:
        await asyncio.wait_for(machine.authed.wait(), 5)
        assert calls == [False, True]
        assert [m["AuthToken"] for m in machine.received] == ["expired-token", GOOD_TOKEN]
    finally:
        await local.async_stop()
        await server.close()


@_ALLOW_AIOHTTP_LATE_HEARTBEAT
async def test_commands_wait_for_a_session(machine_tls) -> None:
    """Nothing is sent before the machine has accepted the token."""
    machine, server, local, _ = await _connect(machine_tls, ["wrong", "still-wrong"])
    try:
        await asyncio.sleep(0.3)
        assert not local.is_connected
        with pytest.raises(DaedalusCommandError) as err:
            await local.async_turn_on()
        assert err.value.reason == "not_connected"
        assert all(m["Message"] == "auth" for m in machine.received)
    finally:
        await local.async_stop()
        await server.close()


async def _connected(machine_tls):
    machine, server, local, _ = await _connect(machine_tls, [GOOD_TOKEN])
    await asyncio.wait_for(machine.authed.wait(), 5)
    for _ in range(100):
        if local.is_connected:
            break
        await asyncio.sleep(0.01)
    return machine, server, local


async def test_declined_command_raises_rejected(machine_tls) -> None:
    """Waking a machine that's already on: the real one answers KO."""
    machine, server, local = await _connected(machine_tls)
    machine.reply = "KO"
    try:
        with pytest.raises(DaedalusCommandError) as err:
            await local.async_turn_on()
        assert err.value.reason == "rejected"
    finally:
        await local.async_stop()
        await server.close()


async def test_silent_machine_raises_no_reply(machine_tls, monkeypatch) -> None:
    machine, server, local = await _connected(machine_tls)
    machine.reply = None
    monkeypatch.setattr(websocket_module, "DEFAULT_TIMEOUT", 0.2)
    try:
        with pytest.raises(DaedalusCommandError) as err:
            await local.async_turn_on()
        assert err.value.reason == "no_reply"
    finally:
        await local.async_stop()
        await server.close()


async def test_unauthorized_answer_without_request_id(machine_tls) -> None:
    """The machine's UNAUTHORIZED answer carries no RequestId; match it by message."""
    machine, server, local = await _connected(machine_tls)
    real_send = local._ws.send_json

    async def drop_connection_id(payload, **kwargs):
        payload = {k: v for k, v in payload.items() if k != "ConnectionId"}
        await real_send(payload, **kwargs)

    local._ws.send_json = drop_connection_id
    try:
        with pytest.raises(DaedalusCommandError) as err:
            await local.async_turn_on()
        assert err.value.reason == "unauthorized"
    finally:
        await local.async_stop()
        await server.close()
