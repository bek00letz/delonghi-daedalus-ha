"""Tests for finding the coffee machine on the home network."""

from __future__ import annotations

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.delonghi_daedalus.api.discovery import (
    async_find_machine,
    hosts_on_networks,
    looks_like_machine,
)
from tests.fake_machine import GOOD_TOKEN, SERIAL, FakeMachine, make_tls

pytestmark = pytest.mark.usefixtures("socket_enabled")


def test_hosts_on_a_home_network() -> None:
    hosts = hosts_on_networks([("192.168.1.10", 24)])
    assert len(hosts) == 253  # 254 usable addresses, minus our own
    assert "192.168.1.10" not in hosts
    assert hosts[0] == "192.168.1.1" and hosts[-1] == "192.168.1.254"


def test_huge_network_is_narrowed_to_the_local_block() -> None:
    hosts = hosts_on_networks([("10.20.30.40", 8)])
    assert len(hosts) == 253
    assert all(h.startswith("10.20.30.") for h in hosts)


def test_ipv6_and_duplicates_are_skipped() -> None:
    assert hosts_on_networks([("fe80::1", 64)]) == []
    assert len(hosts_on_networks([("192.168.1.10", 24), ("192.168.1.11", 24)])) == 252


async def _serve(tmp_path, common_name: str = "AWS IoT Certificate"):
    ctx, fingerprint = make_tls(tmp_path, common_name)
    machine = FakeMachine()
    app = web.Application()
    app.router.add_get("/ws/lan2lan", machine.handler)
    server = TestServer(app)
    await server.start_server(ssl=ctx)
    return machine, server, fingerprint


async def test_known_certificate_is_found_without_sending_the_token(tmp_path) -> None:
    machine, server, fingerprint = await _serve(tmp_path)
    try:
        async with ClientSession() as session:
            found = await async_find_machine(
                session, ["127.0.0.1"], SERIAL, fingerprint=fingerprint, port=server.port
            )
        assert found == ("127.0.0.1", fingerprint)
        assert machine.received == []
    finally:
        await server.close()


async def test_first_time_the_machine_is_confirmed_by_the_token(tmp_path) -> None:
    machine, server, fingerprint = await _serve(tmp_path)

    async def token() -> str:
        return GOOD_TOKEN

    try:
        async with ClientSession() as session:
            found = await async_find_machine(
                session, ["127.0.0.1"], SERIAL, token_provider=token, port=server.port
            )
        assert found == ("127.0.0.1", fingerprint)
    finally:
        await server.close()


async def test_a_machine_that_refuses_the_token_is_not_ours(tmp_path) -> None:
    machine, server, _ = await _serve(tmp_path)

    async def token() -> str:
        return "someone-elses-token"

    try:
        async with ClientSession() as session:
            found = await async_find_machine(
                session, ["127.0.0.1"], SERIAL, token_provider=token, port=server.port
            )
        assert found is None
    finally:
        await server.close()


async def test_other_devices_never_get_the_token(tmp_path) -> None:
    """A NAS or printer with its own certificate isn't even asked."""
    machine, server, _ = await _serve(tmp_path, common_name="Synology NAS")
    asked: list[bool] = []

    async def token() -> str:
        asked.append(True)
        return GOOD_TOKEN

    try:
        async with ClientSession() as session:
            found = await async_find_machine(
                session, ["127.0.0.1"], SERIAL, token_provider=token, port=server.port
            )
        assert found is None
        assert asked == []
        assert machine.received == []
    finally:
        await server.close()


async def test_nothing_listening_is_not_found() -> None:
    async with ClientSession() as session:
        assert await async_find_machine(session, ["127.0.0.1"], SERIAL, fingerprint=b"x", port=9) is None


def test_certificate_name_check(tmp_path) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    make_tls(tmp_path)
    der = x509.load_pem_x509_certificate((tmp_path / "cert.pem").read_bytes()).public_bytes(
        serialization.Encoding.DER
    )
    assert looks_like_machine(der)
    assert not looks_like_machine(b"not a certificate")
