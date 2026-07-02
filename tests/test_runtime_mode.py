import pytest

from src.config.settings import load_okx_connection_settings
from src.runtime.mode import RuntimeMode, parse_runtime_mode
from src.runtime.live_preflight import simulation_preflight_report


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, RuntimeMode.SIMULATION),
        ("simulation", RuntimeMode.SIMULATION),
        ("dry-run", RuntimeMode.SIMULATION),
        ("demo", RuntimeMode.DEMO),
        ("live-safe", RuntimeMode.LIVE_SAFE),
        ("live_armed", RuntimeMode.LIVE_ARMED),
    ],
)
def test_runtime_mode_parsing(value, expected):
    assert parse_runtime_mode(value) is expected


def test_runtime_mode_capabilities_are_explicit():
    assert not RuntimeMode.SIMULATION.touches_exchange
    assert RuntimeMode.DEMO.okx_flag == "1" and RuntimeMode.DEMO.submits_orders
    assert RuntimeMode.LIVE_SAFE.okx_flag == "0" and not RuntimeMode.LIVE_SAFE.submits_orders
    assert RuntimeMode.LIVE_ARMED.okx_flag == "0" and RuntimeMode.LIVE_ARMED.submits_orders


def test_mode_based_credentials_cannot_cross_environments(monkeypatch):
    monkeypatch.setenv("OKX_API_KEY", "production")
    monkeypatch.setenv("OKX_API_SECRET", "production-secret")
    monkeypatch.setenv("OKX_PASSPHRASE", "production-pass")
    monkeypatch.setenv("DEMO_OKX_API_KEY", "demo")
    monkeypatch.setenv("DEMO_OKX_API_SECRET", "demo-secret")
    monkeypatch.setenv("DEMO_OKX_PASSPHRASE", "demo-pass")

    demo = load_okx_connection_settings(runtime_mode="demo")
    safe = load_okx_connection_settings(runtime_mode="live_safe")

    assert demo.api_key == "demo"
    assert safe.api_key == "production"
    with pytest.raises(ValueError, match="conflict"):
        load_okx_connection_settings("1", runtime_mode="live_armed")


def test_simulation_preflight_contract_names_applied_checks():
    report = simulation_preflight_report()
    assert report["credential_environment"] == "none"
    assert report["applicable_checks"] == ["local_replay", "exchange_disabled"]
