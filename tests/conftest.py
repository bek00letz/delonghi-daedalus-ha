"""Shared test setup for the De'Longhi Daedalus integration.

Uses Home Assistant's own test harness (pytest-homeassistant-custom-component),
installed from requirements_test.txt.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load this integration from custom_components/."""
    return
