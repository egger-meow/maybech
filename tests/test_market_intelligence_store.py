from src.market_intelligence.models import MetricObservation, ProviderSyncRun
from src.market_intelligence.storage.metric_store import MetricStore


def _observation(**overrides) -> MetricObservation:
    defaults = dict(
        metric_id="crypto_fear_greed",
        observed_at="2026-07-10T00:00:00+00:00",
        value=50.0,
        unit="index_0_100",
        source_provider="alternative_me",
        source_reference="https://api.alternative.me/fng/",
        fetched_at="2026-07-10T00:05:00+00:00",
        quality="raw",
        is_estimated=False,
        methodology_version="alternative_me_v1",
        metadata={"classification": "Neutral"},
    )
    defaults.update(overrides)
    return MetricObservation(**defaults)


def test_schema_is_versioned(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    assert store.applied_schema_versions() == [1]


def test_observations_survive_a_fresh_store_instance_against_the_same_path(tmp_path):
    """Simulates a process restart: a brand new MetricStore must see prior history."""
    db_path = str(tmp_path / "trades.db")
    MetricStore(db_path).insert_observations([_observation()])

    reopened = MetricStore(db_path)

    assert reopened.latest("crypto_fear_greed").value == 50.0
    assert reopened.applied_schema_versions() == [1]


def test_insert_observations_is_idempotent(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    obs = _observation()

    first = store.insert_observations([obs])
    second = store.insert_observations([obs])

    assert first == 1
    assert second == 0
    assert store.latest("crypto_fear_greed").value == 50.0


def test_insert_observations_conflicting_payload_same_key_is_ignored_not_overwritten(tmp_path):
    """Same (metric_id, source_provider, observed_at) with a different value must not overwrite."""
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_observation(value=50.0)])
    store.insert_observations([_observation(value=999.0)])

    assert store.latest("crypto_fear_greed").value == 50.0


def test_history_orders_ascending_and_bounds_limit(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [
            _observation(observed_at="2026-07-08T00:00:00+00:00", value=40.0),
            _observation(observed_at="2026-07-09T00:00:00+00:00", value=45.0),
            _observation(observed_at="2026-07-10T00:00:00+00:00", value=50.0),
        ]
    )

    history = store.history("crypto_fear_greed")
    assert [obs.value for obs in history] == [40.0, 45.0, 50.0]

    bounded = store.history("crypto_fear_greed", limit=2)
    assert len(bounded) == 2

    filtered = store.history("crypto_fear_greed", start="2026-07-09T00:00:00+00:00")
    assert [obs.value for obs in filtered] == [45.0, 50.0]


def test_history_limit_is_capped_even_when_caller_requests_more(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    assert store.history("crypto_fear_greed", limit=999999) is not None  # does not raise / hang


def test_latest_returns_none_for_unknown_metric(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    assert store.latest("nonexistent_metric") is None


def _run(**overrides) -> ProviderSyncRun:
    defaults = dict(
        provider_id="alternative_me",
        started_at="2026-07-10T00:00:00+00:00",
        completed_at="2026-07-10T00:00:01+00:00",
        status="success",
        records_fetched=1,
        records_stored=1,
    )
    defaults.update(overrides)
    return ProviderSyncRun(**defaults)


def test_latest_sync_run_and_recent_sync_runs(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.record_sync_run(_run(started_at="2026-07-10T00:00:00+00:00"))
    store.record_sync_run(_run(started_at="2026-07-10T01:00:00+00:00", status="failed", error_category="timeout"))

    latest = store.latest_sync_run("alternative_me")
    assert latest.status == "failed"
    assert latest.error_category == "timeout"

    recent = store.recent_sync_runs("alternative_me")
    assert len(recent) == 2


def test_consecutive_failures_counts_since_last_success_and_ignores_skips(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.record_sync_run(_run(started_at="2026-07-10T00:00:00+00:00", status="success"))
    store.record_sync_run(_run(started_at="2026-07-10T01:00:00+00:00", status="skipped"))
    store.record_sync_run(_run(started_at="2026-07-10T02:00:00+00:00", status="failed"))
    store.record_sync_run(_run(started_at="2026-07-10T03:00:00+00:00", status="failed"))

    assert store.consecutive_failures("alternative_me") == 2


def test_consecutive_failures_is_zero_when_most_recent_is_success(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.record_sync_run(_run(started_at="2026-07-10T00:00:00+00:00", status="failed"))
    store.record_sync_run(_run(started_at="2026-07-10T01:00:00+00:00", status="success"))

    assert store.consecutive_failures("alternative_me") == 0
