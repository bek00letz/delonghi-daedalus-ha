"""Shared test setup for the De'Longhi Daedalus integration.

Uses Home Assistant's own test harness (pytest-homeassistant-custom-component),
installed from requirements_test.txt.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load this integration from custom_components/."""
    return


class StubCloud:
    """Stands in for the cloud connection: no network; tests push records in."""

    instances: list[StubCloud] = []

    def __init__(self, session, thing_name: str, token_provider, on_shadow, **kwargs: Any) -> None:
        self.thing_name = thing_name
        self.on_shadow: Callable[[str, dict[str, Any]], None] = on_shadow
        self.on_connected = kwargs.get("on_connected")
        self.kwargs_on_message = kwargs.get("on_machine_message")
        self.is_connected = False
        StubCloud.instances.append(self)

    async def async_start(self) -> None:
        return None

    async def async_stop(self) -> None:
        return None


@pytest.fixture(autouse=True)
def stub_cloud():
    """Every test gets the stub instead of a real AWS connection."""
    StubCloud.instances = []
    with patch(
        "custom_components.delonghi_daedalus.coordinator.DaedalusCloudConnection", StubCloud
    ):
        yield StubCloud


class StubContent:
    """Stands in for De'Longhi's content server: tests set ``drinks`` (or leave it offline)."""

    drinks: list | None = None
    calls: int = 0

    @classmethod
    async def fetch(cls, session, token, machine, language, country):
        from custom_components.delonghi_daedalus.api.client import DaedalusConnectionError

        cls.calls += 1
        if cls.drinks is None:
            raise DaedalusConnectionError("offline in tests")
        return list(cls.drinks)


@pytest.fixture(autouse=True)
def stub_content():
    """No test reaches De'Longhi's content server."""
    StubContent.drinks = None
    StubContent.calls = 0
    with patch("custom_components.delonghi_daedalus.drinks.async_fetch_drinks", StubContent.fetch):
        yield StubContent
