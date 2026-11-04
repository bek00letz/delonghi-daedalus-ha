"""Tests for finding the coffee machine on the home network without giving anything away."""

from __future__ import annotations

from aiohttp import ClientSession, WSMsgType, web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.delonghi_daedalus.api.discovery import (
    async_accepts_token,
    async_find_candidates,
    async_find_machine,
    hosts_on_networks,
    looks_like_machine,
)
from custom_components.delonghi_daedalus.local_network import is_local_address
from tests.fake_machine import GOOD_TOKEN, SERIAL, FakeMachine, make_tls

pytestmark = pytest.mark.usefixtures("socket_enabled")


class Impostor:
    """Any device on the home network that copies the machine's certificate name."""

    def __init__(self) -> None:
        self.stolen: list[str] = []

    async def handler(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == WSMsgType.TEXT and msg.json().get("Message") == "auth":
                self.stolen.append(msg.json()["AuthToken"])
                await ws.send_json({"Message": "auth", "Response": "OK", "ConnectionId": 1})
        return ws


async def _serve(tmp_path, handler, common_name: str = "AWS IoT Certificate"):
    ctx, fingerprint = make_tls(tmp_path, common_name)
    app = web.Application()
    app.router.add_get("/ws/lan2lan", handler)
    server = TestServer(app)
    await server.start_server(ssl=ctx)
    return server, fingerprint


def test_hosts_on_a_home_network() -> None:
    hosts = hosts_on_networks([("192.168.1.10", 24)])
    assert len(hosts) == 253  # 254 usable addresses, minus our own
    assert "192.168.1.10" not in hosts


def test_huge_network_is_narrowed_to_the_local_block() -> None:
    hosts = hosts_on_networks([("10.20.30.40", 8)])
    assert len(hosts) == 253
    assert all(h.startswith("10.20.30.") for h in hosts)


def test_ipv6_and_duplicates_are_skipped() -> None:
    assert hosts_on_networks([("fe80::1", 64)]) == []
    assert len(hosts_on_networks([("192.168.1.10", 24), ("192.168.1.11", 24)])) == 252


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("192.168.1.50", True),
        ("10.0.0.7", True),
        ("evil.example.com", False),
        ("192.168.1.50:443", False),
        ("8.8.8.8", False),  # not a home network
        ("127.0.0.1", False),
        ("", False),
    ],
)
def test_only_plain_home_network_addresses_are_accepted(value: str, ok: bool) -> None:
    assert is_local_address(value) is ok


async def test_search_never_sends_the_token(tmp_path) -> None:
    """A device copying the machine's certificate name is a candidate, and learns nothing."""
    impostor = Impostor()
    server, fingerprint = await _serve(tmp_path, impostor.handler)
    try:
        found = await async_find_candidates(["127.0.0.1"], port=server.port)
        assert found == [("127.0.0.1", fingerprint)]
        assert impostor.stolen == []
    finally:
        await server.close()


async def test_other_devices_are_not_candidates(tmp_path) -> None:
    server, _ = await _serve(tmp_path, Impostor().handler, common_name="Synology NAS")
    try:
        assert await async_find_candidates(["127.0.0.1"], port=server.port) == []
    finally:
        await server.close()


async def test_known_machine_is_found_by_certificate_alone(tmp_path) -> None:
    machine = FakeMachine()
    server, fingerprint = await _serve(tmp_path, machine.handler)
    try:
        assert await async_find_machine(["127.0.0.1"], fingerprint, port=server.port) == "127.0.0.1"
        assert await async_find_machine(["127.0.0.1"], b"\0" * 32, port=server.port) is None
        assert machine.received == []
    finally:
        await server.close()


async def test_confirmed_device_is_checked_with_the_token(tmp_path) -> None:
    machine = FakeMachine()
    server, fingerprint = await _serve(tmp_path, machine.handler)
    try:
        async with ClientSession() as session:
            assert await async_accepts_token(
                session, "127.0.0.1", fingerprint, SERIAL, GOOD_TOKEN, port=server.port
            )
            assert not await async_accepts_token(
                session, "127.0.0.1", fingerprint, SERIAL, "someone-elses", port=server.port
            )
    finally:
        await server.close()


async def test_the_token_goes_only_to_the_exact_certificate_confirmed(tmp_path) -> None:
    """If another device took the address after the person confirmed, it gets nothing."""
    impostor = Impostor()
    server, _ = await _serve(tmp_path, impostor.handler)
    try:
        async with ClientSession() as session:
            assert not await async_accepts_token(
                session, "127.0.0.1", b"\x01" * 32, SERIAL, GOOD_TOKEN, port=server.port
            )
        assert impostor.stolen == []
    finally:
        await server.close()


async def test_nothing_listening_is_not_found() -> None:
    assert await async_find_candidates(["127.0.0.1"], port=9) == []


def test_certificate_name_check(tmp_path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    make_tls(tmp_path)
    der = x509.load_pem_x509_certificate((tmp_path / "cert.pem").read_bytes()).public_bytes(
        serialization.Encoding.DER
    )
    assert looks_like_machine(der)
    assert not looks_like_machine(b"not a certificate")
