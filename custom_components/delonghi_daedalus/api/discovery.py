"""Find the coffee machine on the home network.

The machine doesn't announce itself (no mDNS), so this looks at every address
on the given networks for a TLS server on port 443:

- If the machine's certificate fingerprint is already known, a matching
  certificate is enough. No token is sent anywhere during the search.
- Otherwise only servers whose certificate is named like the machine's
  ("AWS IoT Certificate") are tried, and one counts as found only when it
  accepts the account token for this serial number, which only the right
  machine does. The token goes only to the exact server whose certificate
  was just seen.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
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
PROBE_TIMEOUT = 2.0
AUTH_TIMEOUT = 5.0
CONCURRENCY = 128
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


async def async_probe_certificate(host: str, port: int = DEFAULT_WS_PORT) -> bytes | None:
    """The DER certificate of a TLS server at host:port, or None if there isn't one."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port, ssl=_trust_any_context()), PROBE_TIMEOUT
        )
    except (OSError, TimeoutError, ssl.SSLError):
        return None
    try:
        ssl_object = writer.get_extra_info("ssl_object")
        return ssl_object.getpeercert(binary_form=True) if ssl_object else None
    finally:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), 1)
        except (OSError, TimeoutError, ssl.SSLError):
            pass


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


async def async_find_machine(
    session: aiohttp.ClientSession,
    hosts: Iterable[str],
    serial_number: str,
    *,
    fingerprint: bytes | None = None,
    token_provider: Callable[[], Awaitable[str]] | None = None,
    port: int = DEFAULT_WS_PORT,
) -> tuple[str, bytes] | None:
    """Find the machine; return (address, certificate fingerprint), or None.

    With ``fingerprint``, matches on the certificate alone. Without it,
    ``token_provider`` is needed to confirm a candidate.
    """
    hosts = list(hosts)
    limit = asyncio.Semaphore(CONCURRENCY)

    async def probe(host: str) -> tuple[str, bytes] | None:
        async with limit:
            der = await async_probe_certificate(host, port)
        return (host, der) if der else None

    found = [r for r in await asyncio.gather(*(probe(h) for h in hosts)) if r]
    _LOGGER.debug("Found %d TLS servers on port %d among %d addresses", len(found), port, len(hosts))

    if fingerprint is not None:
        for host, der in found:
            if hashlib.sha256(der).digest() == fingerprint:
                return host, fingerprint
        return None

    if token_provider is None:
        return None
    token: str | None = None
    for host, der in found:
        if not looks_like_machine(der):
            continue
        token = token or await token_provider()
        candidate = hashlib.sha256(der).digest()
        if await async_accepts_token(session, host, candidate, serial_number, token, port):
            return host, candidate
    return None
