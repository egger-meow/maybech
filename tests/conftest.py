"""Global test isolation from developer-machine runtime configuration."""

from dataclasses import replace

import pytest


@pytest.fixture(autouse=True)
def isolate_api_authentication_from_local_env(monkeypatch):
    """Tests opt into authentication explicitly instead of inheriting `.env`."""
    from src.api import app as api_app

    monkeypatch.setattr(
        api_app,
        "settings",
        replace(api_app.settings, MAYBECH_API_TOKEN=""),
    )
