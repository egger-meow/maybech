from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.daemon.service import DaemonRunner
from src.market_intelligence.models import MetricObservation
from src.market_intelligence.service import MarketIntelligenceService
from src.market_intelligence.storage.metric_store import MetricStore

_RECENT = datetime.now(timezone.utc).isoformat()


def _observation(value=65.0, observed_at=_RECENT) -> MetricObservation:
    return MetricObservation(
        metric_id="crypto_fear_greed",
        observed_at=observed_at,
        value=value,
        unit="index_0_100",
        source_provider="alternative_me",
        source_reference="https://api.alternative.me/fng/",
        fetched_at=observed_at,
        quality="raw",
        is_estimated=False,
        methodology_version="alternative_me_v1",
        metadata={"classification": "Greed"},
    )


def _client_with_seeded_service(tmp_path, monkeypatch, *, seed=True):
    store = MetricStore(str(tmp_path / "trades.db"))
    if seed:
        store.insert_observations([_observation()])
    service = MarketIntelligenceService(store=store, providers=[])
    monkeypatch.setattr("src.api.app.MarketIntelligenceService", lambda: service)
    return TestClient(create_app(DaemonRunner()))


def test_get_metrics_lists_registry_with_seeded_value(tmp_path, monkeypatch):
    client = _client_with_seeded_service(tmp_path, monkeypatch)

    response = client.get("/market/metrics")

    assert response.status_code == 200
    body = response.json()
    metric_ids = {metric["metric_id"] for metric in body["metrics"]}
    assert "crypto_fear_greed" in metric_ids
    fear_greed = next(m for m in body["metrics"] if m["metric_id"] == "crypto_fear_greed")
    assert fear_greed["value"] == 65.0
    assert fear_greed["freshness"] == "fresh"


def test_get_metrics_reports_unavailable_before_ingestion(tmp_path, monkeypatch):
    client = _client_with_seeded_service(tmp_path, monkeypatch, seed=False)

    response = client.get("/market/metrics")

    body = response.json()
    fear_greed = next(m for m in body["metrics"] if m["metric_id"] == "crypto_fear_greed")
    assert fear_greed["value"] is None
    assert fear_greed["freshness"] == "unavailable"
    assert fear_greed["unavailable_reason"] == "no observation ingested yet"


def test_get_metric_by_id(tmp_path, monkeypatch):
    client = _client_with_seeded_service(tmp_path, monkeypatch)

    response = client.get("/market/metrics/crypto_fear_greed")

    assert response.status_code == 200
    assert response.json()["value"] == 65.0


def test_get_metric_unknown_id_returns_404(tmp_path, monkeypatch):
    client = _client_with_seeded_service(tmp_path, monkeypatch)

    response = client.get("/market/metrics/not_a_real_metric")

    assert response.status_code == 404


def test_get_series_returns_bounded_history(tmp_path, monkeypatch):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [
            _observation(value=40.0, observed_at="2026-07-08T00:00:00+00:00"),
            _observation(value=65.0, observed_at="2026-07-10T00:00:00+00:00"),
        ]
    )
    service = MarketIntelligenceService(store=store, providers=[])
    monkeypatch.setattr("src.api.app.MarketIntelligenceService", lambda: service)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/market/series/crypto_fear_greed")

    assert response.status_code == 200
    body = response.json()
    assert [p["value"] for p in body["points"]] == [40.0, 65.0]


def test_get_series_unknown_id_returns_404(tmp_path, monkeypatch):
    client = _client_with_seeded_service(tmp_path, monkeypatch)

    response = client.get("/market/series/not_a_real_metric")

    assert response.status_code == 404


def test_get_provider_status_reports_empty_when_no_providers_registered(tmp_path, monkeypatch):
    client = _client_with_seeded_service(tmp_path, monkeypatch)

    response = client.get("/market/providers/status")

    assert response.status_code == 200
    assert response.json() == {"providers": []}


def test_get_regime_returns_six_pillars(tmp_path, monkeypatch):
    client = _client_with_seeded_service(tmp_path, monkeypatch)

    response = client.get("/market/regime")

    assert response.status_code == 200
    body = response.json()
    assert len(body["pillars"]) == 6
    sentiment = next(p for p in body["pillars"] if p["pillar"] == "sentiment")
    assert sentiment["state"] == "cautious"  # seeded fear_greed=65.0 -> leaning greedy band


def test_get_regime_honors_at_query_param(tmp_path, monkeypatch):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [_observation(value=15.0, observed_at="2026-07-01T00:00:00+00:00")]
    )
    service = MarketIntelligenceService(store=store, providers=[])
    monkeypatch.setattr("src.api.app.MarketIntelligenceService", lambda: service)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/market/regime", params={"at": "2026-07-01T00:00:00+00:00"})

    assert response.status_code == 200
    sentiment = next(p for p in response.json()["pillars"] if p["pillar"] == "sentiment")
    assert sentiment["state"] == "stressed"


def test_provider_failure_does_not_break_metrics_endpoint(tmp_path, monkeypatch):
    """A provider outage must degrade its own metric, not the whole response."""
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_observation()])
    service = MarketIntelligenceService(store=store, providers=[])
    monkeypatch.setattr("src.api.app.MarketIntelligenceService", lambda: service)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/market/metrics")

    assert response.status_code == 200
    metrics_by_id = {m["metric_id"]: m for m in response.json()["metrics"]}
    assert metrics_by_id["crypto_fear_greed"]["value"] == 65.0
    assert metrics_by_id["btc_mvrv_z"]["value"] is None
    assert metrics_by_id["btc_mvrv_z"]["unavailable_reason"] == "no observation ingested yet"
