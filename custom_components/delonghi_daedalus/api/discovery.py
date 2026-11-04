"""Find the coffee machine on the home network, without giving anything away.

The machine doesn't announce itself (no mDNS), so this looks at every address
on the given networks for a TLS server on port 443. Nothing here ever sends
the account token to a device that hasn't been confirmed:

- ``async_find_candidates`` collects servers whose certificate is named like
  the machine's ("AWS IoT Certificate"). That name can be copied, so these
  are only candidates for the person to confirm.
- ``async_accepts_token`` checks the ONE device the person confirmed, pinned
  to the exact certificate just seen.
- ``async_find_machine`` re-finds a machine that is already trusted, by its
  certificate fingerprint alone (e.g. after the router gave it a new address).
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import hashlib
import ipaddress
import json
import logging
import ssl

import aiohttp
from cryptography import x509
from cryptography.x509.oid import NameOID

from ..const import DEFAULT_WS_PATH, DEFAULT_WS_PORT, MESSAGE_AUTH, RESPONSE_OK

_LOGGER = logging.getLogger(__name__)

MACHINE_CERT_NAME = "AWS IoT Certificate"
# Two passes, because the machine's Wi-Fi chip is slow to finish a TLS
# handshake when the network is busy (seen 2026-11-04: a 2 s handshake limit
# with many probes in flight missed it): first a cheap check of which
# addresses accept a connection on the port, then a patient TLS handshake
# with only those few.
CONNECT_TIMEOUT = 1.5
CONNECT_CONCURRENCY = 64
PROBE_TIMEOUT = 6.0
PROBE_CONCURRENCY = 4
AUTH_TIMEOUT = 8.0
# Never scan more than this many addresses on one network (a /22).
MAX_HOSTS_PER_NETWORK = 1022


def hosts_on_networks(networks: Iterable[tuple[str, int]]) -> list[str]:
    """Every host address on the given (own address, prefix length) networks.

    Networks bigger than a /22 are narrowed to the /24 around our own address,
    so a large office network doesn't turn into a scan of thousands of hosts.
    The given own addresses are left out.
    """
    own: set[str] = set()
    hosts: list[str] = []
    for address, prefix in networks:
        own.add(address)
        net = ipaddress.ip_network(f"{address}/{prefix}", strict=False)
        if net.version != 4:
            continue
        if net.num_addresses - 2 > MAX_HOSTS_PER_NETWORK:
            net = ipaddress.ip_network(f"{address}/24", strict=False)
        hosts.extend(str(ip) for ip in net.hosts())
    return [h for h in dict.fromkeys(hosts) if h not in own]


def _trust_any_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 1)
    except (OSError, TimeoutError, ssl.SSLError):
        pass


async def _connect(host: str, port: int) -> asyncio.StreamWriter | None:
    """A plain TCP connection, or None if nothing accepts one quickly."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)
    except (OSError, TimeoutError):
        return None
    return writer


async def _certificate_over(writer: asyncio.StreamWriter) -> bytes | None:
    """Upgrade an open connection to TLS (patiently) and return the server's certificate."""
    try:
        await writer.start_tls(_trust_any_context(), ssl_handshake_timeout=PROBE_TIMEOUT)
        ssl_object = writer.get_extra_info("ssl_object")
        return ssl_object.getpeercert(binary_form=True) if ssl_object else None
    except (OSError, TimeoutError, ssl.SSLError):
        return None
    finally:
        await _close(writer)


async def async_probe_certificate(host: str, port: int = DEFAULT_WS_PORT) -> bytes | None:
    """The DER certificate of a TLS server at host:port, or None if there isn't one."""
    writer = await _connect(host, port)
    return await _certificate_over(writer) if writer else None


def looks_like_machine(der: bytes) -> bool:
    """True if the certificate is named like a Daedalus coffee machine's."""
    try:
        names = x509.load_der_x509_certificate(der).subject.get_attributes_for_oid(
            NameOID.COMMON_NAME
        )
    except ValueError:
        return False
    return any(n.value == MACHINE_CERT_NAME for n in names)


async def async_accepts_token(
    session: aiohttp.ClientSession,
    host: str,
    fingerprint: bytes,
    serial_number: str,
    token: str,
    port: int = DEFAULT_WS_PORT,
) -> bool:
    """True if the server with exactly this certificate accepts the token for the serial."""
    try:
        async with asyncio.timeout(AUTH_TIMEOUT):
            async with session.ws_connect(
                f"wss://{host}:{port}{DEFAULT_WS_PATH}", ssl=aiohttp.Fingerprint(fingerprint)
            ) as ws:
                await ws.send_json(
                    {"Message": MESSAGE_AUTH, "SerialNo": serial_number, "AuthToken": token}
                )
                reply = json.loads((await ws.receive()).data)
                return (
                    reply.get("Message") == MESSAGE_AUTH and reply.get("Response") == RESPONSE_OK
                )
    except (aiohttp.ClientError, OSError, TimeoutError, ValueError, TypeError):
        return False


async def _probe_all(hosts: list[str], port: int) -> list[tuple[str, bytes]]:
    """Certificates of the TLS servers among ``hosts``, in the order given.

    Pass one opens plain connections to every address, many at once (cheap).
    Pass two upgrades only the open ones to TLS, a few at a time and patiently,
    on the same connection, so nothing is dialled twice or cut off mid-way.
    """
    connect_limit = asyncio.Semaphore(CONNECT_CONCURRENCY)
    probe_limit = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def connect(host: str) -> asyncio.StreamWriter | None:
        async with connect_limit:
            return await _connect(host, port)

    writers = await asyncio.gather(*(connect(h) for h in hosts))
    open_ = [(host, writer) for host, writer in zip(hosts, writers) if writer]

    async def probe(host: str, writer: asyncio.StreamWriter) -> tuple[str, bytes] | None:
        async with probe_limit:
            der = await _certificate_over(writer)
        return (host, der) if der else None

    found = [r for r in await asyncio.gather(*(probe(h, w) for h, w in open_)) if r]
    _LOGGER.debug(
        "%d of %d addresses accept connections on port %d; %d presented a certificate",
        len(open_), len(hosts), port, len(found),
    )
    return found


async def async_find_candidates(
    hosts: Iterable[str], port: int = DEFAULT_WS_PORT
) -> list[tuple[str, bytes]]:
    """Devices that look like a coffee machine: (address, certificate fingerprint).

    Sends nothing but a TLS handshake. The result must be confirmed by the
    person before the token goes to any of them.
    """
    return [
        (host, hashlib.sha256(der).digest())
        for host, der in await _probe_all(list(hosts), port)
        if looks_like_machine(der)
    ]


async def async_find_machine(
    hosts: Iterable[str],
    fingerprint: bytes,
    port: int = DEFAULT_WS_PORT,
) -> str | None:
    """The address of the already-trusted machine, matched by certificate alone."""
    for host, der in await _probe_all(list(hosts), port):
        if hashlib.sha256(der).digest() == fingerprint:
            return host
    return None
