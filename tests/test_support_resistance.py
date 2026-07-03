from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.data.candles import CandleManager
from src.daemon.service import DaemonRunner
from src.market.support_resistance import SupportResistanceService, analyze_candles


NOW = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)


def test_candle_manager_enforces_row_and_market_key_bounds():
    class Client:
        def get_candles(self, *, inst_id, bar, limit):
            del bar, limit
            base = {"BTC": 100, "ETH": 200, "SOL": 300}[inst_id]
            return [
                [str(1_700_000_000_000 + index * 60_000), str(base + index), str(base + index + 1), str(base + index - 1), str(base + index), "10", "10", "10", "1"]
                for index in range(5)
            ]

    manager = CandleManager(Client(), max_cache_rows=3, max_cache_keys=2)
    manager.fetch("BTC", limit=5)
    manager.fetch("ETH", limit=5)
    manager.fetch("SOL", limit=5)

    assert list(manager._cache) == ["ETH:1m", "SOL:1m"]
    assert all(len(frame) == 3 for frame in manager._cache.values())


def _candles(*, duplicate: bool = False, gap: bool = False) -> pd.DataFrame:
    rows = []
    prices = [100, 98, 96, 98, 101, 104, 106, 104, 101, 99, 97, 99, 102]
    for index, close in enumerate(prices):
        minute = index + (1 if gap and index >= 7 else 0)
        rows.append(
            {
                "timestamp": NOW - timedelta(minutes=len(prices) - minute),
                "open": close - 0.5,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 10 + index,
                "confirm": 1,
            }
        )
    if duplicate:
        rows.append(dict(rows[4]))
    return pd.DataFrame(rows)


def _long_candles(count: int = 100) -> pd.DataFrame:
    rows = []
    for index in range(count):
        close = 100 + (index % 10 if (index // 10) % 2 == 0 else 10 - index % 10)
        rows.append({
            "timestamp": NOW - timedelta(minutes=count - index),
            "open": close - .25,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 10 + index % 7,
            "confirm": 1,
        })
    return pd.DataFrame(rows)


def test_analysis_returns_structured_research_evidence():
    result = analyze_candles(_candles(), inst_id="BTC-USDT-SWAP", bar="1m", now=NOW)

    assert result["status"] == "fresh"
    assert {level["kind"] for level in result["levels"]} == {"support", "resistance"}
    assert all("volume_ratio" in level["evidence"] for level in result["levels"])
    assert all(level["state"] in {"active", "invalidated"} for level in result["levels"])
    assert all("invalidation_rule" in level["evidence"] for level in result["levels"])
    assert result["research_only"] is True
    assert result["eligible_as_live_rule"] is False


def test_analysis_composes_btc_regime_without_making_it_authoritative():
    result = analyze_candles(
        _candles(),
        inst_id="ETH-USDT-SWAP",
        bar="1m",
        now=NOW,
        btc_regime={"direction": "bullish", "symbol": "BTC-USDT-SWAP"},
    )

    support = next(level for level in result["levels"] if level["kind"] == "support")
    resistance = next(level for level in result["levels"] if level["kind"] == "resistance")
    assert support["evidence"]["btc_regime_alignment"] == "aligned"
    assert resistance["evidence"]["btc_regime_alignment"] == "conflicting"
    assert result["context"]["btc_direction"] == "bullish"
    assert result["eligible_as_live_rule"] is False


def test_analysis_exposes_duplicate_missing_and_stale_data():
    result = analyze_candles(
        _candles(duplicate=True, gap=True),
        inst_id="BTC-USDT-SWAP",
        bar="1m",
        now=NOW + timedelta(minutes=10),
    )

    assert result["status"] == "partial"
    assert result["quality"]["duplicate_candles"] == 1
    assert result["quality"]["missing_candles"] == 1
    assert result["freshness"]["stale"] is True


def test_service_bounds_fetch_and_reuses_cached_result(monkeypatch):
    calls = []

    class FakeManager:
        def __init__(self, client):
            assert client == "client"

        def fetch(self, inst_id, bar="1m", limit=100):
            calls.append((inst_id, bar, limit))
            return _long_candles().tail(limit)

    monkeypatch.setattr("src.market.support_resistance.CandleManager", FakeManager)
    service = SupportResistanceService(lambda: "client", now=lambda: NOW)

    first = service.analyze("BTC-USDT-SWAP", bar="1m", limit=100)
    second = service.analyze("BTC-USDT-SWAP", bar="1m", limit=100)

    assert calls == [("BTC-USDT-SWAP", "1m", 100)]
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True


def test_service_refreshes_with_bounded_overlap_and_reuses_state_for_context_change(monkeypatch):
    calls = []
    clock = [NOW]

    class FakeManager:
        def __init__(self, client):
            del client

        def fetch(self, inst_id, bar="1m", limit=100):
            calls.append((inst_id, bar, limit))
            return _long_candles().tail(limit)

    monkeypatch.setattr("src.market.support_resistance.CandleManager", FakeManager)
    service = SupportResistanceService(
        lambda: object(), cache_ttl=timedelta(seconds=15), now=lambda: clock[0]
    )

    initial = service.analyze("BTC-USDT-SWAP", bar="1m", limit=100)
    clock[0] += timedelta(seconds=16)
    incremental = service.analyze("BTC-USDT-SWAP", bar="1m", limit=100)
    contextual = service.analyze(
        "BTC-USDT-SWAP", bar="1m", limit=100,
        btc_regime={"direction": "bullish"},
    )

    assert calls == [
        ("BTC-USDT-SWAP", "1m", 100),
        ("BTC-USDT-SWAP", "1m", 32),
    ]
    assert initial["computation"]["fetch_mode"] == "full"
    assert incremental["computation"]["fetch_mode"] == "incremental_overlap"
    assert incremental["computation"]["pivot_scan_candles"] < initial["computation"]["pivot_scan_candles"]
    assert incremental["computation"]["reused_pivots"] > 0
    assert [
        (item["kind"], item["price"], item["touches"], item["score"], item["state"])
        for item in incremental["levels"]
    ] == [
        (item["kind"], item["price"], item["touches"], item["score"], item["state"])
        for item in initial["levels"]
    ]
    assert incremental["computation"]["state_capacity_candles"] == 300
    assert contextual["computation"]["fetch_mode"] == "state_reuse"
    assert contextual["computation"]["pivot_scan_candles"] == 0
    assert contextual["context"]["btc_direction"] == "bullish"


def test_service_reports_api_failure_as_visible_unavailable_state(monkeypatch):
    class FailingManager:
        def __init__(self, client):
            del client

        def fetch(self, inst_id, bar="1m", limit=100):
            raise RuntimeError("public candle API timed out")

    monkeypatch.setattr("src.market.support_resistance.CandleManager", FailingManager)
    result = SupportResistanceService(lambda: object(), now=lambda: NOW).analyze(
        "BTC-USDT-SWAP", bar="1m", limit=100
    )

    assert result["status"] == "unavailable"
    assert result["levels"] == []
    assert "timed out" in result["errors"][0]


def test_api_exposes_typed_support_resistance_analysis(monkeypatch):
    class FakeManager:
        def __init__(self, client):
            del client

        def fetch(self, inst_id, bar="1m", limit=100):
            assert (inst_id, bar, limit) == ("BTC-USDT-SWAP", "1m", 100)
            return _candles()

    monkeypatch.setattr("src.market.support_resistance.CandleManager", FakeManager)
    client = TestClient(create_app(DaemonRunner(), api_token=""))

    response = client.get(
        "/market/analysis/support-resistance"
        "?inst_id=BTC-USDT-SWAP&bar=1m&limit=100"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["inst_id"] == "BTC-USDT-SWAP"
    assert body["research_only"] is True
    assert body["eligible_as_live_rule"] is False
