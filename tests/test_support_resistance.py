from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.daemon.service import DaemonRunner
from src.market.support_resistance import SupportResistanceService, analyze_candles


NOW = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)


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
            return _candles()

    monkeypatch.setattr("src.market.support_resistance.CandleManager", FakeManager)
    service = SupportResistanceService(lambda: "client", now=lambda: NOW)

    first = service.analyze("BTC-USDT-SWAP", bar="1m", limit=100)
    second = service.analyze("BTC-USDT-SWAP", bar="1m", limit=100)

    assert calls == [("BTC-USDT-SWAP", "1m", 100)]
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True


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
