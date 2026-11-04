"""Tests for how the cloud client reads De'Longhi's identity and device endpoints.

The SAP Gigya endpoints answer with ``Content-Type: text/javascript`` even
though the body is JSON. These tests serve responses exactly that way so the
client never again mistakes a valid answer for a network failure.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer
import pytest

from custom_components.delonghi_daedalus.api import client as client_module
from tests.helpers import DAY, make_token
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
            patch.object(client_module, "GIGYA_LOGOUT_URL", f"{base}/accounts.logout"),
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
        await client.async_sign_in("user@example.com", "right")
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


async def test_unexpected_token_request_error_is_not_a_sign_in_problem() -> None:
    async def login(_: web.Request) -> web.Response:
        return _session()

    async def jwt(_: web.Request) -> web.Response:
        return _gigya({"errorCode": 500001, "errorDetails": "General server error"}, 500)

    async def scenario(client: DaedalusCloudClient):
        await client.async_sign_in("user@example.com", "right")

    with pytest.raises(DaedalusApiError, match="500001") as err:
        await _run({"/accounts.login": login, "/accounts.getJWT": jwt}, scenario)
    assert not isinstance(err.value, DaedalusAuthError)


async def test_an_ended_session_means_sign_in_again() -> None:
    """De'Longhi answering "unauthorized" to a token request: the saved session ended."""

    async def login(_: web.Request) -> web.Response:
        return _session()

    async def jwt(_: web.Request) -> web.Response:
        return _gigya({"errorCode": 403005, "errorDetails": "Unauthorized user"}, 403)

    async def scenario(client: DaedalusCloudClient):
        await client.async_sign_in("user@example.com", "right")

    with pytest.raises(DaedalusAuthError, match="403005"):
        await _run({"/accounts.login": login, "/accounts.getJWT": jwt}, scenario)


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
    """A rejected token gets a fresh one from the saved session (no password), then a retry."""
    calls = {"login": 0, "jwt": 0, "devices": 0}

    async def login(_: web.Request) -> web.Response:
        calls["login"] += 1
        return _session(calls["login"])

    async def jwt(request: web.Request) -> web.Response:
        calls["jwt"] += 1
        answer = _strict_jwt(request)
        if calls["jwt"] > 1 and answer.status == 200:  # a new token each time
            return _gigya({"errorCode": 0, "id_token": f"jwt-renewed-{calls['jwt']}"})
        return answer

    async def devices(request: web.Request) -> web.Response:
        calls["devices"] += 1
        if request.headers["Authorization"] == "Bearer jwt-1":
            return web.json_response({"message": "Forbidden"}, status=403)
        return web.json_response({"ownedByMe": [{"serialNumber": "SN1"}]})

    async def scenario(client: DaedalusCloudClient):
        await client.async_sign_in("user@example.com", "right")
        return await client.async_get_devices()

    found = await _run(
        {"/accounts.login": login, "/accounts.getJWT": jwt, "/devices": devices}, scenario
    )
    assert [d.serial_number for d in found] == ["SN1"]
    assert calls == {"login": 1, "jwt": 2, "devices": 2}  # signed in once, renewed once


async def test_token_rejected_twice_is_an_auth_error() -> None:
    """If a fresh sign-in still doesn't help, stop and report it instead of looping."""

    async def login(_: web.Request) -> web.Response:
        return _session()

    async def jwt(request: web.Request) -> web.Response:
        return _strict_jwt(request)

    async def devices(_: web.Request) -> web.Response:
        return web.json_response({"message": "Unauthorized"}, status=401)

    async def scenario(client: DaedalusCloudClient):
        await client.async_sign_in("user@example.com", "right")
        await client.async_get_devices()

    with pytest.raises(DaedalusAuthError):
        await _run(
            {"/accounts.login": login, "/accounts.getJWT": jwt, "/devices": devices}, scenario
        )



# --- the renewal gate: what keeps a changed password from locking the account ---


def _client_with_session(**kwargs) -> DaedalusCloudClient:
    return DaedalusCloudClient(gigya_session=("st", "ss"), **kwargs)


async def test_a_refused_session_stops_all_further_attempts() -> None:
    client = _client_with_session()
    get_jwt = AsyncMock(side_effect=DaedalusAuthError("403005"))
    with patch.object(client, "async_get_jwt", get_jwt):
        for _ in range(5):
            with pytest.raises(DaedalusAuthError):
                await client.async_get_token(refresh=True)
    assert get_jwt.await_count == 1  # one try, then it waits for the person to sign in again


async def test_a_locked_account_waits_before_trying_again() -> None:
    client = _client_with_session()
    get_jwt = AsyncMock(side_effect=DaedalusAccountLockedError("403120"))
    with patch.object(client, "async_get_jwt", get_jwt):
        for _ in range(3):
            with pytest.raises(DaedalusAccountLockedError):
                await client.async_get_token(refresh=True)
    assert get_jwt.await_count == 1


async def test_simultaneous_renewals_become_one() -> None:
    """The local link, the cloud link and the coordinator asking at once: one renewal."""
    client = _client_with_session()

    async def renew() -> str:
        await asyncio.sleep(0.01)
        client._jwt_token = make_token(90 * DAY)
        return client._jwt_token

    get_jwt = AsyncMock(side_effect=renew)
    with patch.object(client, "async_get_jwt", get_jwt):
        await asyncio.gather(*(client.async_get_token(refresh=True) for _ in range(3)))
    assert get_jwt.await_count == 1


async def test_sign_out_ends_the_session() -> None:
    seen: dict[str, str] = {}

    async def logout(request: web.Request) -> web.Response:
        seen["secret"] = request.query.get("secret", "")
        seen["bearer"] = request.headers.get("Authorization", "")
        return _gigya({"errorCode": 0})

    async def scenario(client: DaedalusCloudClient):
        client._session_token, client._session_secret = "st", "ss"
        await client.async_sign_out()
        return client.gigya_session

    remaining = await _run({"/accounts.logout": logout}, scenario)
    assert seen == {"secret": "ss", "bearer": "Bearer st"}
    assert remaining is None


def test_errors_never_carry_the_session_secret() -> None:
    from custom_components.delonghi_daedalus.api.client import _redact

    text = "500, url='https://accounts.eu1.gigya.com/accounts.getJWT?apiKey=k&secret=abc123&x=1'"
    assert "abc123" not in _redact(text)
