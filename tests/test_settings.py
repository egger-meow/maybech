import pytest

from src.config.settings import load_okx_connection_settings


def test_demo_mode_selects_only_demo_credentials(monkeypatch):
    monkeypatch.setenv("OKX_API_KEY", "production-key")
    monkeypatch.setenv("OKX_API_SECRET", "production-secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "production-passphrase")
    monkeypatch.setenv("DEMO_OKX_API_KEY", "demo-key")
    monkeypatch.setenv("DEMO_OKX_API_SECRET", "demo-secret")
    monkeypatch.setenv("DEMO_OKX_PASSPHRASE", "demo-passphrase")

    selected = load_okx_connection_settings("1")

    assert selected.execution_mode == "demo"
    assert (selected.api_key, selected.api_secret, selected.passphrase) == (
        "demo-key",
        "demo-secret",
        "demo-passphrase",
    )


def test_production_mode_selects_only_production_credentials(monkeypatch):
    monkeypatch.setenv("OKX_API_KEY", "production-key")
    monkeypatch.setenv("OKX_API_SECRET", "production-secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "production-passphrase")
    monkeypatch.setenv("DEMO_OKX_API_KEY", "demo-key")
    monkeypatch.setenv("DEMO_OKX_API_SECRET", "demo-secret")
    monkeypatch.setenv("DEMO_OKX_PASSPHRASE", "demo-passphrase")

    selected = load_okx_connection_settings("0")

    assert selected.execution_mode == "production"
    assert (selected.api_key, selected.api_secret, selected.passphrase) == (
        "production-key",
        "production-secret",
        "production-passphrase",
    )


def test_unknown_okx_mode_is_rejected():
    with pytest.raises(ValueError, match="OKX_FLAG"):
        load_okx_connection_settings("invalid")
