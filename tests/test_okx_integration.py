"""
Integration tests — calls real OKX API (read-only, no orders placed).

Prerequisites:
  - .env file with valid OKX_API_KEY, OKX_API_SECRET, OKX_PASSPHRASE
  - OKX_FLAG=1 (demo mode recommended)

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

# Skip entire module if API keys are not configured
_has_keys = bool(
    settings.OKX_API_KEY
    and settings.OKX_API_SECRET
    and settings.OKX_PASSPHRASE
)
pytestmark = pytest.mark.skipif(
    not _has_keys,
    reason="OKX API keys not configured in .env — skipping integration tests",
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

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
    assert "currencies" in summary
    print(f"\n  Total Equity: {summary['total_equity']}")
    print(f"  Available Equity: {summary['available_equity']}")
    if summary["currencies"]:
        print(f"  Currencies: {[c['ccy'] for c in summary['currencies']]}")


# ── CandleMiner Integration Tests ───────────────────────────────────────────

def test_candle_miner_peak_valley_real_data(candle_manager):
    """Fetch real 1H candles and verify CandleMiner produces clustered peak_valley output."""
    from src.data.candle_miner import CandleMiner, PeakValley

    df = candle_manager.fetch(inst_id="ETH-USDT", bar="1H", limit=100)
    assert len(df) >= 10, "Need enough candles for meaningful analysis"

    miner = CandleMiner()
    miner.register(PeakValley(window=2))
    result = miner.mine(df)

    assert "peak_valley" in result
    pv = result["peak_valley"]
    assert "raw" in pv and "levels" in pv

    # Raw extrema
    raw = pv["raw"]
    assert len(raw) > 0, f"Expected extrema in real data, got none (candles={len(df)})"
    for ex in raw:
        assert "price" in ex and isinstance(ex["price"], float)
        assert "sharpness" in ex and ex["sharpness"] >= 0
        assert "kind" in ex and ex["kind"] in ("peak", "valley")

    # Clustered levels
    levels = pv["levels"]
    assert len(levels) > 0, "Expected price levels from real data"
    for lv in levels:
        assert 0 <= lv["significance"] <= 1.0
        assert lv["kind"] in ("peak", "valley", "mixed")
        assert lv["count"] >= 1

    peaks = [e for e in raw if e["kind"] == "peak"]
    valleys = [e for e in raw if e["kind"] == "valley"]
    print(f"\n  Candles: {len(df)}")
    print(f"  Raw: {len(peaks)} peaks, {len(valleys)} valleys")
    print(f"  Clustered levels: {len(levels)}")
    for lv in levels[:5]:
        print(f"    ${lv['price']:.2f}  sig={lv['significance']:.3f}  "
              f"kind={lv['kind']}  count={lv['count']}  purity={lv['purity']:.2f}")


def test_candle_miner_multiple_timeframes(candle_manager):
    """Run PeakValley on two timeframes and compare."""
    from src.data.candle_miner import PeakValley

    df_1m = candle_manager.fetch(inst_id="ETH-USDT", bar="1m", limit=100)
    df_1h = candle_manager.fetch(inst_id="ETH-USDT", bar="1H", limit=100)

    pv = PeakValley(window=2)
    result_1m = pv.extract(df_1m)
    result_1h = pv.extract(df_1h)

    assert isinstance(result_1m["raw"], list)
    assert isinstance(result_1h["levels"], list)

    print(f"\n  1m: {len(result_1m['raw'])} raw extrema → {len(result_1m['levels'])} levels")
    print(f"  1H: {len(result_1h['raw'])} raw extrema → {len(result_1h['levels'])} levels")


# ── Notificator Integration Tests ───────────────────────────────────────────

def test_notificator_service_tick(candle_manager):
    """Verify that NotificatorService can perform a full tick on real data."""
    from src.daemon.notificator_service import NotificatorService
    from unittest.mock import MagicMock

    service = NotificatorService()
    service.setup()
    
    # Mock the notifier to avoid sending real messages during test, 
    # but still let the logic run.
    service.notifier.send = MagicMock(return_value=True)
    
    # Run one tick
    service.tick()
    
    # If the tick passed without exception, it's a success for this level of test.
    # We can also check if it attempted to send anything (might not if no proximity).
    print(f"\n  Notificator TICK completed. Alerts triggered: {service.notifier.send.call_count}")


def test_strategy_service_setup_and_tick():
    """Verify that StrategyService can setup and perform a tick on real data."""
    from src.daemon.strategy_service import StrategyService

    # Use dry_run for safety
    service = StrategyService(dry_run=True)
    service.setup()
    
    # Run one tick
    service.tick()
    
    print("\n  Strategy TICK completed.")
