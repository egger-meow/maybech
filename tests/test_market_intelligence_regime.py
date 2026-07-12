from datetime import datetime, timedelta, timezone

from src.market_intelligence.models import MetricObservation
from src.market_intelligence.regime import rules
from src.market_intelligence.regime.assessor import assess_all
from src.market_intelligence.service import MarketIntelligenceService
from src.market_intelligence.storage.metric_store import MetricStore

_NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)


def _obs(metric_id, value, observed_at, unit="usd", metadata=None) -> MetricObservation:
    return MetricObservation(
        metric_id=metric_id,
        observed_at=observed_at,
        value=value,
        unit=unit,
        source_provider="test",
        source_reference="test",
        fetched_at=observed_at,
        quality="raw",
        is_estimated=False,
        methodology_version="test_v1",
        metadata=metadata or {},
    )


# --- rules.py: pure classification, no store involved -----------------------


def test_classify_derivatives_unavailable_without_data():
    state, summary = rules.classify_derivatives(None)
    assert state == "unavailable"


def test_classify_derivatives_bands():
    assert rules.classify_derivatives(0.01)[0] == "supportive"
    assert rules.classify_derivatives(0.10)[0] == "neutral"
    assert rules.classify_derivatives(0.30)[0] == "cautious"
    assert rules.classify_derivatives(0.60)[0] == "stressed"
    assert rules.classify_derivatives(-0.60)[0] == "stressed"  # magnitude, not direction


def test_classify_valuation_bands():
    assert rules.classify_valuation(None)[0] == "unavailable"
    assert rules.classify_valuation(-1.0)[0] == "supportive"
    assert rules.classify_valuation(1.5)[0] == "neutral"
    assert rules.classify_valuation(4.0)[0] == "cautious"
    assert rules.classify_valuation(7.0)[0] == "stressed"


def test_classify_liquidity_bands():
    assert rules.classify_liquidity(None)[0] == "unavailable"
    assert rules.classify_liquidity(3.0)[0] == "supportive"
    assert rules.classify_liquidity(0.0)[0] == "neutral"
    assert rules.classify_liquidity(-3.0)[0] == "cautious"
    assert rules.classify_liquidity(-6.0)[0] == "stressed"


def test_classify_sentiment_bands():
    assert rules.classify_sentiment(None)[0] == "unavailable"
    assert rules.classify_sentiment(10)[0] == "stressed"
    assert rules.classify_sentiment(30)[0] == "cautious"
    assert rules.classify_sentiment(50)[0] == "neutral"
    assert rules.classify_sentiment(70)[0] == "cautious"
    assert rules.classify_sentiment(90)[0] == "stressed"


def test_classify_price_breadth_bands():
    assert rules.classify_price_breadth(None)[0] == "unavailable"
    assert rules.classify_price_breadth(70.0)[0] == "supportive"
    assert rules.classify_price_breadth(50.0)[0] == "neutral"
    assert rules.classify_price_breadth(30.0)[0] == "cautious"
    assert rules.classify_price_breadth(10.0)[0] == "stressed"


# --- assessor.py: reads the store, bounded by `at` --------------------------


def test_assess_all_returns_six_pillars_in_stable_order(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))

    assessments = assess_all(store, at=_NOW)

    pillars = [a.pillar for a in assessments]
    assert pillars == ["derivatives", "valuation", "price_breadth", "holder_behavior", "liquidity", "sentiment"]


def test_assess_all_reports_unavailable_with_zero_confidence_when_empty(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))

    assessments = assess_all(store, at=_NOW)

    for assessment in assessments:
        assert assessment.state == "unavailable"
        assert assessment.confidence == 0.0
        assert assessment.evidence == []


def test_holder_behavior_is_always_unavailable(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_obs("okx_funding_annualized", 0.01, _NOW.isoformat(), unit="rate")])

    assessments = {a.pillar: a for a in assess_all(store, at=_NOW)}

    assert assessments["holder_behavior"].state == "unavailable"


def test_price_breadth_becomes_available_once_metric_is_ingested(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [_obs("market_breadth_advancing_pct", 70.0, _NOW.isoformat(), unit="pct")]
    )

    assessments = {a.pillar: a for a in assess_all(store, at=_NOW)}

    assert assessments["price_breadth"].state == "supportive"
    assert assessments["price_breadth"].confidence == 1.0


def test_assess_derivatives_uses_latest_funding_and_includes_regime_evidence(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_obs("okx_funding_annualized", 0.01, _NOW.isoformat(), unit="rate")])
    store.insert_observations(
        [_obs("okx_price_oi_regime", 1.0, _NOW.isoformat(), unit="regime_code", metadata={"label": "new longs"})]
    )

    assessments = {a.pillar: a for a in assess_all(store, at=_NOW)}
    derivatives = assessments["derivatives"]

    assert derivatives.state == "supportive"
    assert derivatives.confidence == 1.0
    evidence_metric_ids = {e["metric_id"] for e in derivatives.evidence}
    assert evidence_metric_ids == {"okx_funding_annualized", "okx_price_oi_regime"}


def test_assess_valuation_confidence_degrades_with_staleness(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    # btc_mvrv_z ttl is 2 days; 10 days old should be very_stale (0.3 confidence)
    stale_time = (_NOW - timedelta(days=10)).isoformat()
    store.insert_observations([_obs("btc_mvrv_z", 1.0, stale_time, unit="z_score")])

    assessments = {a.pillar: a for a in assess_all(store, at=_NOW)}
    valuation = assessments["valuation"]

    assert valuation.state == "neutral"
    assert valuation.confidence == 0.3


def test_assess_replay_ignores_observations_after_at(tmp_path):
    """An assessment at historical time T must not see data recorded after T."""
    store = MetricStore(str(tmp_path / "trades.db"))
    past = _NOW - timedelta(days=5)
    future = _NOW + timedelta(days=1)
    store.insert_observations([_obs("crypto_fear_greed", 50.0, past.isoformat(), unit="index_0_100")])
    store.insert_observations([_obs("crypto_fear_greed", 95.0, future.isoformat(), unit="index_0_100")])

    assessments = {a.pillar: a for a in assess_all(store, at=_NOW)}

    assert assessments["sentiment"].summary.startswith("Fear & Greed Index 50")


def test_assess_at_same_timestamp_is_reproducible(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_obs("crypto_fear_greed", 15.0, _NOW.isoformat(), unit="index_0_100")])

    first = assess_all(store, at=_NOW)
    second = assess_all(store, at=_NOW)

    first_sentiment = next(a for a in first if a.pillar == "sentiment")
    second_sentiment = next(a for a in second if a.pillar == "sentiment")
    assert first_sentiment.state == second_sentiment.state == "stressed"
    assert first_sentiment.summary == second_sentiment.summary


# --- service.get_regime ------------------------------------------------------


def test_service_get_regime_defaults_to_now(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    service = MarketIntelligenceService(store=store, providers=[])

    regime = service.get_regime()

    assert len(regime["pillars"]) == 6
    assert regime["at"] is not None


def test_service_get_regime_falls_back_to_now_on_unparseable_at(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    service = MarketIntelligenceService(store=store, providers=[])

    regime = service.get_regime(at="not-a-timestamp")

    assert len(regime["pillars"]) == 6


def test_service_get_regime_honors_explicit_at(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_obs("crypto_fear_greed", 15.0, _NOW.isoformat(), unit="index_0_100")])
    service = MarketIntelligenceService(store=store, providers=[])

    regime = service.get_regime(at=_NOW.isoformat())

    sentiment = next(p for p in regime["pillars"] if p["pillar"] == "sentiment")
    assert sentiment["state"] == "stressed"
