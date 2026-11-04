"""Asynchronous client for De'Longhi Daedalus Cloud Services (Gigya SSO & AWS Gateway)."""

from __future__ import annotations

import base64
from collections.abc import Callable
import json
import logging
import time
from typing import Any

import aiohttp

from ..const import (
    AWS_GATEWAY_URL,
    DEFAULT_TIMEOUT,
    GIGYA_API_KEY,
    GIGYA_JWT_EXPIRATION,
    GIGYA_JWT_URL,
    GIGYA_LOGIN_URL,
)
from .models import DaedalusAppliance

_LOGGER = logging.getLogger(__name__)

# Applied to every request: Home Assistant's shared session has no short default.
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)


class DaedalusAuthError(Exception):
    """De'Longhi rejected the email and password."""


class DaedalusAccountLockedError(DaedalusAuthError):
    """De'Longhi paused sign-ins for the account after too many attempts."""


class DaedalusConnectionError(Exception):
    """Raised when connection to cloud endpoints fails."""


class DaedalusApiError(Exception):
    """Raised when an API endpoint returns an unexpected error."""


# Renew the 90-day account token once fewer than this many seconds remain,
# whenever the cloud is reachable. Until it actually expires, the saved token
# keeps control over the home network working without the internet.
TOKEN_RENEW_BEFORE = 30 * 24 * 3600


def token_expiry(token: str) -> float | None:
    """Read the expiry time (seconds since 1970) from a JWT, without verifying it."""
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return float(claims["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return None


# Gigya error codes that mean the credentials themselves were refused.
GIGYA_WRONG_CREDENTIALS = {403042, 403047}
# Too many attempts: the account is temporarily locked or wants a CAPTCHA.
GIGYA_LOCKED = {403120, 401020, 401021, 401030}


def _raise_for_gigya_error(data: dict[str, Any], step: str) -> None:
    """Turn a Gigya error answer into the right exception for its meaning."""
    code = data.get("errorCode", 0)
    if not code:
        return
    details = data.get("errorDetails") or data.get("errorMessage") or "no details"
    message = f"De'Longhi {step} failed (code {code}): {details}"
    if code in GIGYA_LOCKED:
        raise DaedalusAccountLockedError(message)
    if code in GIGYA_WRONG_CREDENTIALS:
        raise DaedalusAuthError(message)
    raise DaedalusApiError(message)


async def _read_json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    """Decode a cloud response body as JSON, whatever its declared content type.

    SAP Gigya labels its JSON answers ``text/javascript``, which aiohttp's
    strict ``resp.json()`` rejects. Reading the body ourselves keeps a valid
    answer from being mistaken for a network failure, and turns a truly
    non-JSON body (a proxy or captive-portal page) into a clear API error.
    """
    text = await resp.text()
    try:
        data = json.loads(text)
    except ValueError as err:
        raise DaedalusApiError(
            f"{resp.url.host} returned a non-JSON response (HTTP {resp.status}): {text[:200]!r}"
        ) from err
    if not isinstance(data, dict):
        raise DaedalusApiError(f"{resp.url.host} returned unexpected JSON: {text[:200]!r}")
    return data


class DaedalusCloudClient:
    """Handles communication with De'Longhi identity and cloud gateways."""

    def __init__(
        self,
        session: aiohttp.ClientSession | None = None,
        *,
        token: str | None = None,
        credentials: tuple[str, str] | None = None,
        on_token: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize the client.

        ``token`` is a previously saved account token; ``credentials`` let it be
        renewed without asking; ``on_token`` is told about every new token so it
        can be saved.
        """
        self._session = session
        self._owns_session = False
        self._jwt_token = token
        self._session_secret: str | None = None
        self._session_token: str | None = None
        self._credentials = credentials
        self._on_token = on_token

    @property
    def token_expires(self) -> float | None:
        """When the current account token expires (seconds since 1970)."""
        return token_expiry(self._jwt_token) if self._jwt_token else None

    def token_valid(self, margin: float = 0) -> bool:
        """True if there is a token with more than ``margin`` seconds left."""
        expires = self.token_expires
        return expires is not None and expires - time.time() > margin

    async def async_authenticate(self, username: str, password: str) -> None:
        """Sign in and get a cloud token: the one sign-in path for every caller.

        The credentials are remembered so an expired token can be renewed
        silently, without asking the person for their password again.
        """
        await self.async_login(username, password)
        await self.async_get_jwt()
        self._credentials = (username, password)

    async def _async_reauthenticate(self) -> bool:
        """Renew the cloud token with remembered credentials. False if there are none."""
        if self._credentials is None:
            return False
        _LOGGER.debug("De'Longhi cloud token rejected; signing in again")
        await self.async_authenticate(*self._credentials)
        return True

    async def async_get_token(self, refresh: bool = False) -> str:
        """Return the account token, renewing it if asked or if it has expired.

        The same token opens the machine's local connection, which checks it
        itself. If renewing fails because the cloud can't be reached, a token
        that hasn't expired yet is still returned: that is what keeps control
        over the home network working without the internet.
        """
        if not refresh and self.token_valid():
            return self._jwt_token  # type: ignore[return-value]
        try:
            if not await self._async_reauthenticate():
                raise DaedalusAuthError("No saved credentials to get a De'Longhi token with")
        except (DaedalusConnectionError, DaedalusApiError):
            if self.token_valid():
                _LOGGER.debug("Couldn't renew the De'Longhi token; using the saved one")
                return self._jwt_token  # type: ignore[return-value]
            raise
        assert self._jwt_token is not None
        return self._jwt_token

    async def async_renew_if_due(self) -> None:
        """Renew the token when it is close to expiring and the cloud is reachable."""
        if self.token_valid(TOKEN_RENEW_BEFORE) or self._credentials is None:
            return
        try:
            await self._async_reauthenticate()
            _LOGGER.debug("Renewed the De'Longhi token ahead of expiry")
        except (DaedalusConnectionError, DaedalusApiError) as err:
            _LOGGER.debug("Couldn't renew the De'Longhi token yet: %s", err)

    async def _get_session(self) -> aiohttp.ClientSession:
        """Retrieve or create active aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=REQUEST_TIMEOUT)
            self._owns_session = True
        return self._session

    async def async_close(self) -> None:
        """Close internally managed aiohttp session."""
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    async def async_login(self, username: str, password: str) -> str:
        """Authenticate with SAP Gigya SSO and return session secret."""
        session = await self._get_session()
        payload = {
            "apiKey": GIGYA_API_KEY,
            "loginID": username,
            "password": password,
            "targetEnv": "mobile",
            "httpStatusCodes": "true",
        }

        try:
            async with session.post(GIGYA_LOGIN_URL, data=payload, timeout=REQUEST_TIMEOUT) as resp:
                data = await _read_json(resp)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise DaedalusConnectionError(f"Network error during Gigya login: {err}") from err

        _raise_for_gigya_error(data, "sign-in")

        session_info = data.get("sessionInfo") or {}
        if not session_info.get("sessionSecret") or not session_info.get("sessionToken"):
            raise DaedalusApiError("De'Longhi sign-in answer had no session")

        self._session_secret = session_info["sessionSecret"]
        self._session_token = session_info["sessionToken"]
        return self._session_secret

    async def async_get_jwt(self) -> str:
        """Exchange the sign-in session for the 90-day account token.

        Sent exactly as the My Coffee Lounge app does (seen 2026-11-04): the
        session secret as a query parameter and the session token as a
        Bearer header. The secret alone is refused with 403010.
        """
        if not self._session_secret or not self._session_token:
            raise DaedalusAuthError("Cannot request a token without signing in first")

        session = await self._get_session()
        params = {
            "apiKey": GIGYA_API_KEY,
            "secret": self._session_secret,
            "expiration": str(GIGYA_JWT_EXPIRATION),
            "httpStatusCodes": "true",
        }
        headers = {"Authorization": f"Bearer {self._session_token}"}

        try:
            async with session.post(
                GIGYA_JWT_URL, params=params, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                data = await _read_json(resp)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise DaedalusConnectionError(f"Network error during JWT exchange: {err}") from err

        _raise_for_gigya_error(data, "token request")

        id_token = data.get("id_token")
        if not id_token:
            raise DaedalusApiError("De'Longhi token answer had no token")

        self._jwt_token = id_token
        if self._on_token is not None:
            self._on_token(id_token)
        return self._jwt_token

    async def async_get_devices(self) -> list[DaedalusAppliance]:
        """Fetch appliances owned by or shared with the account.

        If the cloud rejects the token (it expired, or was revoked), sign in
        again once with the remembered credentials and retry. Only a second
        rejection, or a rejected password, reaches the caller as an auth error.
        """
        try:
            data = await self._async_fetch_devices()
        except DaedalusAuthError:
            if not await self._async_reauthenticate():
                raise
            data = await self._async_fetch_devices()
        return self._parse_devices(data)

    async def _async_fetch_devices(self) -> dict[str, Any]:
        """Make one device-list request with the current token."""
        if not self._jwt_token:
            raise DaedalusAuthError("Cannot fetch devices without active JWT token")

        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {self._jwt_token}",
            "Accept": "application/json",
        }
        url = f"{AWS_GATEWAY_URL}/devices"

        try:
            async with session.get(url, headers=headers, timeout=REQUEST_TIMEOUT) as resp:
                # API Gateway answers 401 for a bad token and 403 when its
                # authorizer denies an expired one; both mean "sign in again".
                if resp.status in (401, 403):
                    raise DaedalusAuthError(
                        f"AWS API Gateway rejected the token (HTTP {resp.status})"
                    )
                if resp.status != 200:
                    text = await resp.text()
                    raise DaedalusApiError(f"AWS API Gateway returned status {resp.status}: {text}")
                return await _read_json(resp)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise DaedalusConnectionError(f"Network error querying devices: {err}") from err

    @staticmethod
    def _parse_devices(data: dict[str, Any]) -> list[DaedalusAppliance]:
        """Turn the gateway's device list into appliances."""
        appliances: list[DaedalusAppliance] = []
        raw_devices = data.get("ownedByMe", []) + data.get("sharedToMe", [])

        for item in raw_devices:
            appliance = DaedalusAppliance(
                machine_name=item.get("machineName", ""),
                serial_number=item.get("serialNumber", ""),
                model=item.get("machineModel", ""),
                sku=item.get("sku", ""),
                status=item.get("status", "UNKNOWN"),
            )
            appliances.append(appliance)

        return appliances

