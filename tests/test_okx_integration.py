"""
Integration tests — calls real OKX API (read-only, no orders placed).

Prerequisites:
  - MAYBECH_RUN_OKX_INTEGRATION=1 explicitly opts into network access
  - .env contains the credential set matching OKX_FLAG
  - OKX_FLAG=1 selects DEMO_OKX_API_KEY/SECRET/PASSPHRASE
  - OKX_FLAG=0 selects OKX_API_KEY/SECRET/PASSPHRASE

Order mutation is blocked inside this module unless both
MAYBECH_RUN_OKX_DEMO_EXECUTION=1 and OKX_FLAG=1 are set. The read-only tests in
this file do not require that execution flag.

Run:
  .venv\\Scripts\\python.exe -m pytest tests/test_okx_integration.py -v
"""

import os
import sys

import pytest

# Ensure project root is on sys.path
_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _root not in sys.path:
    sys.path.insert(0, _root)

from src.config.settings import settings  # noqa: E402


def _call_okx_access(func, *args, **kwargs):
    """Require configured private credentials to authenticate successfully."""
    return func(*args, **kwargs)

# Skip the entire module unless network integration is explicitly requested.
_integration_enabled = os.getenv("MAYBECH_RUN_OKX_INTEGRATION", "0") == "1"
_demo_execution_enabled = os.getenv("MAYBECH_RUN_OKX_DEMO_EXECUTION", "0") == "1"
_has_keys = bool(
    settings.OKX_API_KEY
    and settings.OKX_API_SECRET
    and settings.OKX_PASSPHRASE
)

_ORDER_MUTATION_METHODS = (
    "place_limit_order",
    "cancel_order",
    "place_reduce_market_order",
    "place_position_stop",
    "amend_position_stop",
    "cancel_position_stop",
)
pytestmark = pytest.mark.skipif(
    not (_integration_enabled and _has_keys),
    reason=(
        "OKX integration tests require MAYBECH_RUN_OKX_INTEGRATION=1 and "
        "configured credentials"
    ),
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def require_explicit_demo_execution(monkeypatch):
    """Fail closed if an integration test unexpectedly mutates OKX orders."""
    from src.exchange.client import OKXClient

    if _demo_execution_enabled:
        if settings.OKX_FLAG != "1":
            pytest.fail(
                "MAYBECH_RUN_OKX_DEMO_EXECUTION=1 requires OKX_FLAG=1; "
                "production order mutation is never enabled by this test suite"
            )
        return

    def blocked_order_mutation(*args, **kwargs):
        del args, kwargs
        raise AssertionError(
            "OKX order mutation is blocked; set MAYBECH_RUN_OKX_DEMO_EXECUTION=1 "
            "with OKX_FLAG=1 only for an intentional Demo execution test"
        )

    for method_name in _ORDER_MUTATION_METHODS:
        monkeypatch.setattr(OKXClient, method_name, blocked_order_mutation)

@pytest.fixture(scope="module")
def client():
    """Shared OKXClient for all tests in this module."""
    from src.exchange.client import OKXClient
    return OKXClient()


@pytest.fixture(scope="module")
def candle_manager(client):
    """Shared CandleManager."""
    from src.data.candles import CandleManager
    return CandleManager(client)


@pytest.fixture(scope="module")
def dashboard(client):
    """Shared Dashboard."""
    from src.monitor.dashboard import Dashboard
    return Dashboard(client)


@pytest.mark.skipif(
    _demo_execution_enabled,
    reason="Demo execution was explicitly enabled for this integration run",
)
def test_order_mutation_requires_separate_demo_execution_flag(client):
    """Read-only integration opt-in alone must never permit order mutation."""
    with pytest.raises(AssertionError, match="OKX order mutation is blocked"):
        client.place_limit_order()

# ── Account Tests ────────────────────────────────────────────────────────────

def test_get_balance(client):
    """Balance response contains account equity data."""
    data = _call_okx_access(client.get_balance)
    assert isinstance(data, list)
    assert len(data) >= 1
    acct = data[0]
    assert "totalEq" in acct
    print(f"\n  Total Equity: {acct['totalEq']}")


def test_get_account_config(client):
    """Account config returns level and position mode."""
    data = _call_okx_access(client.get_account_config)
    assert isinstance(data, list)
    assert len(data) >= 1
    config = data[0]
    assert "acctLv" in config
    assert "posMode" in config
    print(f"\n  Account Level: {config['acctLv']}, Pos Mode: {config['posMode']}")


def test_get_positions(client):
    """Positions list is returned (may be empty if no open positions)."""
    data = _call_okx_access(client.get_positions)
    assert isinstance(data, list)
    print(f"\n  Open positions count: {len(data)}")


def test_get_fee_rates(client):
    """Fee rates for SPOT are returned."""
    data = _call_okx_access(client.get_fee_rates, inst_type="SPOT")
    assert isinstance(data, list)
    assert len(data) >= 1
    fees = data[0]
    assert "maker" in fees
    assert "taker" in fees
    print(f"\n  Maker: {fees['maker']}, Taker: {fees['taker']}")


def test_get_interest_limits(client):
    """Borrow interest limits are returned."""
    data = _call_okx_access(client.get_interest_limits, ccy="ETH")
    assert isinstance(data, list)
    assert len(data) >= 1
    print(f"\n  Interest data keys: {list(data[0].keys())}")


# ── Market Data Tests ────────────────────────────────────────────────────────

def test_get_candles_eth(client):
    """Fetch ETH-USDT 1m candles, validate shape."""
    data = client.get_candles(inst_id="ETH-USDT", bar="1m", limit="10")
    assert isinstance(data, list)
    assert len(data) >= 1
    # Each candle should have 9 elements: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    assert len(data[0]) == 9
    print(f"\n  Got {len(data)} ETH-USDT 1m candles")
    print(f"  Latest: ts={data[0][0]}, close={data[0][4]}, vol={data[0][5]}")


def test_get_history_candles(client):
    """Fetch historical ETH-USDT candles."""
    data = client.get_history_candles(inst_id="ETH-USDT", bar="1m", limit="10")
    assert isinstance(data, list)
    assert len(data) >= 1
    assert len(data[0]) == 9
    print(f"\n  Got {len(data)} history candles")


def test_get_ticker(client):
    """Ticker has 'last' price."""
    data = client.get_ticker(inst_id="ETH-USDT")
    assert isinstance(data, list)
    assert len(data) >= 1
    ticker = data[0]
    assert "last" in ticker
    print(f"\n  ETH-USDT last price: {ticker['last']}")


# ── Higher-level Module Tests ────────────────────────────────────────────────

def test_candle_manager_fetch(candle_manager):
    """CandleManager.fetch returns valid DataFrame with correct columns."""
    import pandas as pd

    df = candle_manager.fetch(inst_id="ETH-USDT", bar="1m", limit=20)
    assert isinstance(df, pd.DataFrame)
    assert len(df) >= 1
    assert "timestamp" in df.columns
    assert "open" in df.columns
    assert "close" in df.columns
    assert "volume" in df.columns
    # Verify types
    assert df["close"].dtype == float
    assert df["volume"].dtype == float
    print(f"\n  DataFrame shape: {df.shape}")
    print(f"  Latest candle:\n{df.iloc[-1].to_string()}")


def test_dashboard_summary(dashboard):
    """Dashboard.get_account_summary returns dict with equity fields."""
    summary = _call_okx_access(dashboard.get_account_summary)
    assert isinstance(summary, dict)
    assert "total_equity" in summary
    assert "available_equity" in summary
    assert summary["total_equity_currency"] == "USD"
    assert summary["available_equity_status"] in {
        "account_valued", "per_currency_only", "unavailable",
    }
    assert summary["unrealized_pnl_status"] in {
        "account_valued", "per_currency_only", "unavailable",
    }
    assert "currencies" in summary
    print(f"\n  Total Equity: {summary['total_equity']}")
    print(f"  Available Equity: {summary['available_equity']}")
    if summary["currencies"]:
        assert all(item["native_currency"] == item["ccy"] for item in summary["currencies"])
        assert all("equity_usd" in item for item in summary["currencies"])
        print(f"  Currencies: {[c['ccy'] for c in summary['currencies']]}")


def test_strategy_service_setup_and_tick():
    """Verify that StrategyService can setup and perform a tick on real data."""
    from src.daemon.strategy_service import StrategyService

    # Use dry_run for safety
    service = StrategyService(dry_run=True)
    service.setup()
    
    # Run one tick
    service.tick()
    
    print("\n  Strategy TICK completed.")
