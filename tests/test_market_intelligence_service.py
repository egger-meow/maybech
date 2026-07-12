from datetime import datetime, timedelta, timezone

import pytest

from src.market_intelligence.models import MetricObservation
from src.market_intelligence.providers.base import MarketDataProvider, ProviderError
from src.market_intelligence.service import MarketIntelligenceService, default_providers
from src.market_intelligence.storage.metric_store import MetricStore


class _FakeProvider(MarketDataProvider):
    provider_id = "fake_provider"
    min_refresh_interval_seconds = 300.0
    metric_ids = ("crypto_fear_greed",)

    def __init__(self, *, observations=None, error=None, configured=True):
        self._observations = observations or []
        self._error = error
        self._configured = configured
        self.call_count = 0

    def is_configured(self):
        return self._configured

    def fetch_observations(self):
        self.call_count += 1
        if self._error is not None:
            raise self._error
        return self._observations


def _observation(value=50.0, observed_at="2026-07-10T00:00:00+00:00") -> MetricObservation:
    return MetricObservation(
        metric_id="crypto_fear_greed",
        observed_at=observed_at,
        value=value,
        unit="index_0_100",
        source_provider="fake_provider",
        source_reference="https://example.invalid/",
        fetched_at=observed_at,
        quality="raw",
        is_estimated=False,
        methodology_version="fake_v1",
        metadata={},
    )


def test_sync_provider_success_persists_observations(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    provider = _FakeProvider(observations=[_observation()])
    service = MarketIntelligenceService(store=store, providers=[provider])

    run = service.sync_provider(provider)

    assert run.status == "success"
    assert run.records_stored == 1
    assert store.latest("crypto_fear_greed").value == 50.0


def test_sync_provider_failure_is_isolated_and_recorded(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    provider = _FakeProvider(error=ProviderError("boom", category="timeout"))
    service = MarketIntelligenceService(store=store, providers=[provider])

    run = service.sync_provider(provider)

    assert run.status == "failed"
    assert run.error_category == "timeout"
    assert store.latest("crypto_fear_greed") is None


def test_sync_provider_skips_when_not_due(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    provider = _FakeProvider(observations=[_observation()])
    service = MarketIntelligenceService(store=store, providers=[provider])

    first = service.sync_provider(provider)
    second = service.sync_provider(provider)

    assert first.status == "success"
    assert second.status == "skipped"
    assert provider.call_count == 1


def test_sync_provider_force_bypasses_due_check(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    provider = _FakeProvider(observations=[_observation()])
    service = MarketIntelligenceService(store=store, providers=[provider])

    service.sync_provider(provider)
    forced = service.sync_provider(provider, force=True)

    assert forced.status == "success"
    assert provider.call_count == 2


def test_sync_all_isolates_one_provider_failure_from_another(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    healthy = _FakeProvider(observations=[_observation()])
    healthy.provider_id = "healthy_provider"
    broken = _FakeProvider(error=RuntimeError("boom"))
    broken.provider_id = "broken_provider"
    service = MarketIntelligenceService(store=store, providers=[healthy, broken])

    runs = service.sync_all()

    statuses = {run.provider_id: run.status for run in runs}
    assert statuses == {"healthy_provider": "success", "broken_provider": "failed"}


def test_get_metric_reports_unavailable_before_any_ingestion(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    service = MarketIntelligenceService(store=store, providers=[])

    metric = service.get_metric("crypto_fear_greed")

    assert metric["value"] is None
    assert metric["freshness"] == "unavailable"
    assert metric["unavailable_reason"] == "no observation ingested yet"


def test_get_metric_unknown_id_reports_unavailable(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    service = MarketIntelligenceService(store=store, providers=[])

    metric = service.get_metric("not_a_real_metric")

    assert metric["unavailable_reason"] == "unknown metric_id"


def test_get_metric_freshness_transitions(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    store.insert_observations([_observation(observed_at=(now - timedelta(hours=1)).isoformat())])
    service = MarketIntelligenceService(store=store, providers=[])

    fresh = service.get_metric("crypto_fear_greed", now=now)
    assert fresh["freshness"] == "fresh"  # crypto_fear_greed TTL is 2 days

    very_stale = service.get_metric("crypto_fear_greed", now=now + timedelta(days=10))
    assert very_stale["freshness"] == "very_stale"


def test_get_series_bounds_and_reports_metric_id(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [
            _observation(value=40.0, observed_at="2026-07-08T00:00:00+00:00"),
            _observation(value=50.0, observed_at="2026-07-09T00:00:00+00:00"),
        ]
    )
    service = MarketIntelligenceService(store=store, providers=[])

    series = service.get_series("crypto_fear_greed")

    assert series["metric_id"] == "crypto_fear_greed"
    assert [point["value"] for point in series["points"]] == [40.0, 50.0]
    assert series["unavailable_reason"] is None


def test_get_provider_status_reflects_recent_runs(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    provider = _FakeProvider(observations=[_observation()])
    service = MarketIntelligenceService(store=store, providers=[provider])

    service.sync_provider(provider)
    statuses = service.get_provider_status()

    assert len(statuses) == 1
    assert statuses[0]["provider_id"] == "fake_provider"
    assert statuses[0]["enabled"] is True
    assert statuses[0]["last_success_at"] is not None
    assert statuses[0]["consecutive_failures"] == 0


def test_sync_provider_skips_unconfigured_provider_without_recording_failure(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    provider = _FakeProvider(configured=False)
    service = MarketIntelligenceService(store=store, providers=[provider])

    run = service.sync_provider(provider)

    assert run.status == "skipped"
    assert run.error_category == "not_configured"
    assert provider.call_count == 0
    assert service.store.consecutive_failures("fake_provider") == 0


def test_get_provider_status_reports_enabled_false_for_unconfigured_provider(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    provider = _FakeProvider(configured=False)
    service = MarketIntelligenceService(store=store, providers=[provider])

    service.sync_provider(provider)
    statuses = service.get_provider_status()

    assert statuses[0]["enabled"] is False
    assert statuses[0]["last_attempt_at"] is None  # a skip is not counted as an attempt


def test_default_providers_always_includes_okx_and_defillama_regardless_of_client():
    without_client = default_providers()
    with_client = default_providers(exchange_client=object())

    ids_without = {p.provider_id for p in without_client}
    ids_with = {p.provider_id for p in with_client}
    assert ids_without == ids_with == {
        "alternative_me",
        "bitcoin_data_mvrv",
        "coingecko_global",
        "coingecko_breadth",
        "defillama_stablecoins",
        "okx_market",
    }
    okx_provider = next(p for p in without_client if p.provider_id == "okx_market")
    assert okx_provider.is_configured() is False
    okx_provider_configured = next(p for p in with_client if p.provider_id == "okx_market")
    assert okx_provider_configured.is_configured() is True
