"""Tests for the local /ws/lan2lan connection, against a fake coffee machine.

The fake answers the way a real ECAM 470.85 did on 2026-11-04: the account's
De'Longhi token in AuthToken gets {"Response": "OK", "ConnectionId": n};
anything else gets {"Response": "KO"}.
"""

from __future__ import annotations

import asyncio
import ssl

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.delonghi_daedalus.api import websocket as websocket_module
from custom_components.delonghi_daedalus.api.websocket import (
    DaedalusCommandError,
    DaedalusLocalWebSocket,
)

pytestmark = pytest.mark.usefixtures("socket_enabled")

from tests.fake_machine import GOOD_TOKEN, SERIAL, FakeMachine, make_tls


@pytest.fixture(scope="module")
def machine_cert(tmp_path_factory) -> tuple[ssl.SSLContext, bytes]:
    return make_tls(tmp_path_factory.mktemp("tls"))


@pytest.fixture(scope="module")
def machine_tls(machine_cert) -> ssl.SSLContext:
    return machine_cert[0]


async def _connect(machine_tls, tokens: list[str], **local_kwargs):
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
        host="127.0.0.1",
        serial_number=SERIAL,
        token_provider=provider,
        port=server.port,
        **local_kwargs,
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


async def test_first_machine_to_accept_the_token_is_trusted(machine_tls, machine_cert) -> None:
    saved: list[bytes] = []
    machine, server, local, _ = await _connect(
        machine_tls, [GOOD_TOKEN], on_fingerprint=saved.append
    )
    try:
        await asyncio.wait_for(machine.authed.wait(), 5)
        for _ in range(100):
            if saved:
                break
            await asyncio.sleep(0.01)
        assert saved == [machine_cert[1]]
    finally:
        await local.async_stop()
        await server.close()


async def test_trusted_certificate_connects(machine_tls, machine_cert) -> None:
    machine, server, local, _ = await _connect(
        machine_tls, [GOOD_TOKEN], fingerprint=machine_cert[1]
    )
    try:
        await asyncio.wait_for(machine.authed.wait(), 5)
    finally:
        await local.async_stop()
        await server.close()


async def test_different_certificate_never_gets_the_token(machine_tls) -> None:
    """Another device at the machine's address: refuse, report, and send nothing."""
    changes: list[bool] = []
    machine, server, local, calls = await _connect(
        machine_tls,
        [GOOD_TOKEN],
        fingerprint=b"\x00" * 32,
        on_identity_changed=changes.append,
    )
    try:
        for _ in range(200):
            if changes:
                break
            await asyncio.sleep(0.01)
        assert changes == [True]
        assert local.identity_mismatch
        assert machine.received == []  # the token was never sent
        assert calls == []  # it wasn't even fetched
    finally:
        await local.async_stop()
        await server.close()
