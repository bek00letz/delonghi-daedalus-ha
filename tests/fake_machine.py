"""A fake De'Longhi coffee machine for tests, answering like a real ECAM 470.85 (2026-11-04)."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import ssl
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

SERIAL = "SN0001234567"
GOOD_TOKEN = "fresh-token"




def make_tls(folder: Path, common_name: str = "AWS IoT Certificate") -> tuple[ssl.SSLContext, bytes]:
    """A self-signed server certificate and its SHA-256 fingerprint.

    The real machine's certificate is named "AWS IoT Certificate".
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
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
    return ctx, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).digest()


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


