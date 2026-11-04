"""Asynchronous client for De'Longhi Daedalus Cloud Services (Gigya SSO & AWS Gateway)."""

from __future__ import annotations

import logging
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


class DaedalusAuthError(Exception):
    """Raised when authentication with Gigya SSO fails."""


class DaedalusConnectionError(Exception):
    """Raised when connection to cloud endpoints fails."""


class DaedalusApiError(Exception):
    """Raised when an API endpoint returns an unexpected error."""


class DaedalusCloudClient:
    """Handles communication with De'Longhi identity and cloud gateways."""

    def __init__(self, session: aiohttp.ClientSession | None = None) -> None:
        """Initialize cloud client with an optional shared aiohttp ClientSession."""
        self._session = session
        self._owns_session = False
        self._jwt_token: str | None = None
        self._session_secret: str | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Retrieve or create active aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT)
            )
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
            async with session.post(GIGYA_LOGIN_URL, data=payload) as resp:
                data: dict[str, Any] = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise DaedalusConnectionError(f"Network error during Gigya login: {err}") from err

        error_code = data.get("errorCode", 0)
        if error_code != 0:
            error_details = data.get("errorDetails") or data.get("errorMessage", "Unknown auth failure")
            raise DaedalusAuthError(f"Gigya authentication failed (code {error_code}): {error_details}")

        session_info = data.get("sessionInfo")
        if not session_info or "sessionSecret" not in session_info:
            raise DaedalusAuthError("Gigya login response missing sessionInfo or sessionSecret")

        self._session_secret = session_info["sessionSecret"]
        return self._session_secret

    async def async_get_jwt(self, session_secret: str | None = None) -> str:
        """Exchange session secret for AWS RS256 JWT bearer token."""
        secret = session_secret or self._session_secret
        if not secret:
            raise DaedalusAuthError("Cannot request JWT without an active session secret")

        session = await self._get_session()
        params = {
            "apiKey": GIGYA_API_KEY,
            "secret": secret,
            "expiration": str(GIGYA_JWT_EXPIRATION),
            "fields": "profile.firstName,profile.lastName",
            "httpStatusCodes": "true",
        }

        try:
            async with session.post(GIGYA_JWT_URL, params=params) as resp:
                data: dict[str, Any] = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise DaedalusConnectionError(f"Network error during JWT exchange: {err}") from err

        error_code = data.get("errorCode", 0)
        if error_code != 0:
            error_details = data.get("errorDetails") or data.get("errorMessage", "Unknown failure")
            raise DaedalusAuthError(f"Gigya JWT exchange failed (code {error_code}): {error_details}")

        id_token = data.get("id_token")
        if not id_token:
            raise DaedalusAuthError("Gigya getJWT response did not contain id_token")

        self._jwt_token = id_token
        return self._jwt_token

    async def async_get_devices(self, jwt_token: str | None = None) -> list[DaedalusAppliance]:
        """Fetch list of appliances owned by or shared with user."""
        token = jwt_token or self._jwt_token
        if not token:
            raise DaedalusAuthError("Cannot fetch devices without active JWT token")

        session = await self._get_session()
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        url = f"{AWS_GATEWAY_URL}/devices"

        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 401:
                    raise DaedalusAuthError("AWS API Gateway rejected JWT (401 Unauthorized)")
                if resp.status != 200:
                    text = await resp.text()
                    raise DaedalusApiError(f"AWS API Gateway returned status {resp.status}: {text}")
                data: dict[str, Any] = await resp.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise DaedalusConnectionError(f"Network error querying devices: {err}") from err

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
