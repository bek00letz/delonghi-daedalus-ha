"""Tests for how the cloud client reads De'Longhi's identity and device endpoints.

The SAP Gigya endpoints answer with ``Content-Type: text/javascript`` even
though the body is JSON. These tests serve responses exactly that way so the
client never again mistakes a valid answer for a network failure.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.delonghi_daedalus.api import client as client_module
from custom_components.delonghi_daedalus.api.client import (
    DaedalusAccountLockedError,
    DaedalusApiError,
    DaedalusAuthError,
    DaedalusCloudClient,
    DaedalusConnectionError,
)

# These tests run a real local HTTP server so aiohttp's own content-type
# checks apply exactly as in production; a mocked session would skip them.
pytestmark = pytest.mark.usefixtures("socket_enabled")

GIGYA_CONTENT_TYPE = "text/javascript"


def _gigya(body: dict[str, Any], status: int = 200) -> web.Response:
    """Answer the way Gigya does: JSON body, JavaScript content type."""
    return web.Response(
        status=status,
        text=json.dumps(body),
        content_type=GIGYA_CONTENT_TYPE,
        charset="utf-8",
    )


def _session(n: int | str = 1) -> web.Response:
    """A successful sign-in answer, with both halves of the session."""
    return _gigya(
        {"errorCode": 0, "sessionInfo": {"sessionSecret": f"secret{n}", "sessionToken": f"st{n}"}}
    )


def _strict_jwt(request: web.Request) -> web.Response:
    """Answer getJWT like the real service: the secret alone is refused with 403010.

    Hands out "jwt-<n>" for a matching secret<n> + Bearer st<n> pair.
    """
    secret = request.query.get("secret", "")
    bearer = request.headers.get("Authorization", "")
    n = secret.removeprefix("secret")
    if not secret.startswith("secret") or bearer != f"Bearer st{n}":
        return _gigya({"errorCode": 403010, "errorDetails": "Invalid parameter: secret"}, 403)
    assert "fields" not in request.query, "the token must not carry profile fields"
    return _gigya({"errorCode": 0, "id_token": f"jwt-{n}"})


async def _run(routes: dict[str, Any], scenario) -> Any:
    """Start a local server with ``routes`` and run ``scenario(client)`` against it."""
    app = web.Application()
    for path, handler in routes.items():
        app.router.add_route("*", path, handler)

    server = TestServer(app)
    await server.start_server()
    base = str(server.make_url("")).rstrip("/")
    try:
        with (
            patch.object(client_module, "GIGYA_LOGIN_URL", f"{base}/accounts.login"),
            patch.object(client_module, "GIGYA_JWT_URL", f"{base}/accounts.getJWT"),
            patch.object(client_module, "AWS_GATEWAY_URL", base),
        ):
            async with ClientSession() as session:
                return await scenario(DaedalusCloudClient(session=session))
    finally:
        await server.close()


async def test_login_and_discovery_succeed_with_gigya_content_type() -> None:
    """A correct password goes all the way through to the device list."""

    async def login(_: web.Request) -> web.Response:
        return _session()

    async def jwt(request: web.Request) -> web.Response:
        return _strict_jwt(request)

    async def devices(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer jwt-1"
        return web.json_response(
            {
                "ownedByMe": [
                    {"machineName": "thing", "serialNumber": "SN1", "machineModel": "ECAM"}
                ],
                "sharedToMe": [],
            }
        )

    async def scenario(client: DaedalusCloudClient):
        await client.async_authenticate("user@example.com", "right")
        return await client.async_get_devices()

    found = await _run(
        {"/accounts.login": login, "/accounts.getJWT": jwt, "/devices": devices},
        scenario,
    )
    assert [d.serial_number for d in found] == ["SN1"]


async def test_wrong_password_is_an_auth_error_not_a_connection_error() -> None:
    """Gigya's 403 for a bad password must show "invalid credentials", not "can't connect"."""

    async def login(_: web.Request) -> web.Response:
        return _gigya(
            {
                "errorCode": 403042,
                "errorDetails": "invalid loginID or password",
                "statusCode": 403,
            },
            status=403,
        )

    async def scenario(client: DaedalusCloudClient):
        await client.async_login("user@example.com", "wrong")

    with pytest.raises(DaedalusAuthError, match="403042"):
        await _run({"/accounts.login": login}, scenario)


async def test_token_request_failure_is_not_a_password_error() -> None:
    """A refused token request means something else went wrong, not a wrong password."""

    async def login(_: web.Request) -> web.Response:
        return _session()

    async def jwt(_: web.Request) -> web.Response:
        return _gigya({"errorCode": 403005, "errorDetails": "Unauthorized user"}, 403)

    async def scenario(client: DaedalusCloudClient):
        await client.async_authenticate("user@example.com", "right")

    with pytest.raises(DaedalusApiError, match="403005") as err:
        await _run({"/accounts.login": login, "/accounts.getJWT": jwt}, scenario)
    assert not isinstance(err.value, DaedalusAuthError)


@pytest.mark.parametrize("code", [403120, 401020])
async def test_too_many_attempts_is_account_locked(code: int) -> None:
    async def login(_: web.Request) -> web.Response:
        return _gigya({"errorCode": code, "errorDetails": "Account temporarily locked"}, 403)

    async def scenario(client: DaedalusCloudClient):
        await client.async_login("user@example.com", "pw")

    with pytest.raises(DaedalusAccountLockedError):
        await _run({"/accounts.login": login}, scenario)


async def test_non_json_body_is_an_api_error() -> None:
    """A proxy or captive-portal HTML page is reported as a bad answer, not a crash."""

    async def login(_: web.Request) -> web.Response:
        return web.Response(text="<html>Gateway timeout</html>", content_type="text/html")

    async def scenario(client: DaedalusCloudClient):
        await client.async_login("user@example.com", "pw")

    with pytest.raises(DaedalusApiError):
        await _run({"/accounts.login": login}, scenario)


async def test_unreachable_host_is_a_connection_error() -> None:
    """Only a real network failure maps to "can't connect"."""

    async def scenario() -> None:
        with patch.object(client_module, "GIGYA_LOGIN_URL", "http://127.0.0.1:9/accounts.login"):
            async with ClientSession() as session:
                await DaedalusCloudClient(session=session).async_login("u", "p")

    with pytest.raises(DaedalusConnectionError):
        await scenario()


async def test_expired_token_is_renewed_silently() -> None:
    """A rejected token triggers one quiet sign-in with the saved password, then a retry."""
    calls = {"login": 0, "devices": 0}

    async def login(_: web.Request) -> web.Response:
        calls["login"] += 1
        return _session(calls["login"])

    async def jwt(request: web.Request) -> web.Response:
        return _strict_jwt(request)

    async def devices(request: web.Request) -> web.Response:
        calls["devices"] += 1
        if request.headers["Authorization"] == "Bearer jwt-1":
            return web.json_response({"message": "Forbidden"}, status=403)
        return web.json_response({"ownedByMe": [{"serialNumber": "SN1"}]})

    async def scenario(client: DaedalusCloudClient):
        await client.async_authenticate("user@example.com", "right")
        return await client.async_get_devices()

    found = await _run(
        {"/accounts.login": login, "/accounts.getJWT": jwt, "/devices": devices}, scenario
    )
    assert [d.serial_number for d in found] == ["SN1"]
    assert calls == {"login": 2, "devices": 2}


async def test_token_rejected_twice_is_an_auth_error() -> None:
    """If a fresh sign-in still doesn't help, stop and report it instead of looping."""

    async def login(_: web.Request) -> web.Response:
        return _session()

    async def jwt(request: web.Request) -> web.Response:
        return _strict_jwt(request)

    async def devices(_: web.Request) -> web.Response:
        return web.json_response({"message": "Unauthorized"}, status=401)

    async def scenario(client: DaedalusCloudClient):
        await client.async_authenticate("user@example.com", "right")
        await client.async_get_devices()

    with pytest.raises(DaedalusAuthError):
        await _run(
            {"/accounts.login": login, "/accounts.getJWT": jwt, "/devices": devices}, scenario
        )
