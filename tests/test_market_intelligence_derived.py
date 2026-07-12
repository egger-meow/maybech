from datetime import datetime, timedelta, timezone

from src.market_intelligence.derived.calculators import (
    DaysSinceFearGreedExtremeCalculator,
    FearGreedPercentileCalculator,
    FundingAnnualizedCalculator,
    MvrvPercentileCalculator,
    PercentChangeCalculator,
    PriceOiRegimeCalculator,
    RollingAverageCalculator,
    default_calculators,
)
from src.market_intelligence.models import MetricObservation
from src.market_intelligence.service import MarketIntelligenceService
from src.market_intelligence.storage.metric_store import MetricStore

_NOW = datetime(2026, 7, 12, tzinfo=timezone.utc)


def _obs(metric_id, value, observed_at, unit="usd", source_provider="test") -> MetricObservation:
    return MetricObservation(
        metric_id=metric_id,
        observed_at=observed_at,
        value=value,
        unit=unit,
        source_provider=source_provider,
        source_reference="test",
        fetched_at=observed_at,
        quality="raw",
        is_estimated=False,
        methodology_version="test_v1",
        metadata={},
    )


def test_funding_annualized_returns_none_without_base_metric(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    calculator = FundingAnnualizedCalculator()

    assert calculator.compute(store, now=_NOW) is None


def test_funding_annualized_multiplies_by_fundings_per_year(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_obs("okx_oi_weighted_funding", 0.0002, "2026-07-12T00:00:00+00:00", unit="rate")])
    calculator = FundingAnnualizedCalculator()

    observation = calculator.compute(store, now=_NOW)

    assert observation is not None
    assert round(observation.value, 6) == round(0.0002 * 3 * 365, 6)
    assert observation.metric_id == "okx_funding_annualized"


def test_mvrv_percentile_returns_none_below_min_sample_size(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    for i in range(5):
        store.insert_observations([_obs("btc_mvrv_z", float(i), f"2026-07-0{i+1}T00:00:00+00:00", unit="z_score")])
    calculator = MvrvPercentileCalculator()

    assert calculator.compute(store, now=_NOW) is None


def test_mvrv_percentile_computes_rank_of_latest_value(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    # 10 values 0..9, latest (index 9) has value 9 -> percentile 100
    for i in range(10):
        store.insert_observations(
            [_obs("btc_mvrv_z", float(i), f"2026-07-{i+1:02d}T00:00:00+00:00", unit="z_score")]
        )
    calculator = MvrvPercentileCalculator()

    observation = calculator.compute(store, now=_NOW)

    assert observation is not None
    assert observation.value == 100.0
    assert observation.metadata["sample_size"] == 10


def test_percent_change_returns_none_without_reference_in_window(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_obs("stablecoin_total_mcap_usd", 100.0, "2026-07-12T00:00:00+00:00")])
    calculator = PercentChangeCalculator(
        source_metric_id="stablecoin_total_mcap_usd", metric_id="stablecoin_mcap_change_7d_pct", window_days=7
    )

    assert calculator.compute(store, now=_NOW) is None


def test_percent_change_computes_pct_vs_closest_observation_at_or_before_cutoff(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [
            _obs("stablecoin_total_mcap_usd", 100.0, "2026-07-04T00:00:00+00:00"),
            _obs("stablecoin_total_mcap_usd", 110.0, "2026-07-12T00:00:00+00:00"),
        ]
    )
    calculator = PercentChangeCalculator(
        source_metric_id="stablecoin_total_mcap_usd", metric_id="stablecoin_mcap_change_7d_pct", window_days=7
    )

    observation = calculator.compute(store, now=_NOW)

    assert observation is not None
    assert round(observation.value, 4) == 10.0
    assert observation.metric_id == "stablecoin_mcap_change_7d_pct"


def test_price_oi_regime_returns_none_without_24h_of_history(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_obs("okx_btc_price_usd", 65000.0, "2026-07-12T00:00:00+00:00")])
    store.insert_observations([_obs("okx_btc_oi_usd", 300.0, "2026-07-12T00:00:00+00:00")])
    calculator = PriceOiRegimeCalculator()

    assert calculator.compute(store, now=_NOW) is None


def test_price_oi_regime_classifies_new_longs(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [
            _obs("okx_btc_price_usd", 60000.0, "2026-07-11T00:00:00+00:00"),
            _obs("okx_btc_price_usd", 65000.0, (_NOW).isoformat()),
        ]
    )
    store.insert_observations(
        [
            _obs("okx_btc_oi_usd", 300.0, "2026-07-11T00:00:00+00:00"),
            _obs("okx_btc_oi_usd", 330.0, (_NOW).isoformat()),
        ]
    )
    calculator = PriceOiRegimeCalculator()

    observation = calculator.compute(store, now=_NOW)

    assert observation is not None
    assert observation.value == 1.0
    assert observation.metadata["label"] == "new longs (bullish continuation)"


def test_price_oi_regime_flat_within_deadband(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [
            _obs("okx_btc_price_usd", 65000.0, "2026-07-11T00:00:00+00:00"),
            _obs("okx_btc_price_usd", 65010.0, (_NOW).isoformat()),
        ]
    )
    store.insert_observations(
        [
            _obs("okx_btc_oi_usd", 300.0, "2026-07-11T00:00:00+00:00"),
            _obs("okx_btc_oi_usd", 300.5, (_NOW).isoformat()),
        ]
    )
    calculator = PriceOiRegimeCalculator()

    observation = calculator.compute(store, now=_NOW)

    assert observation is not None
    assert observation.value == 0.0


def test_compute_derived_skips_when_not_due_and_force_bypasses(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_obs("okx_oi_weighted_funding", 0.0002, "2026-07-12T00:00:00+00:00", unit="rate")])
    service = MarketIntelligenceService(store=store, providers=[], calculators=default_calculators())

    first = service.compute_derived()
    second = service.compute_derived()
    forced = service.compute_derived(force=True)

    first_ids = {obs.metric_id for obs in first}
    assert "okx_funding_annualized" in first_ids
    assert second == []
    forced_ids = {obs.metric_id for obs in forced}
    assert "okx_funding_annualized" in forced_ids


def test_sync_all_also_computes_derived_metrics(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations([_obs("okx_oi_weighted_funding", 0.0001, "2026-07-12T00:00:00+00:00", unit="rate")])
    service = MarketIntelligenceService(store=store, providers=[], calculators=default_calculators())

    service.sync_all()

    assert store.latest("okx_funding_annualized") is not None


def test_fear_greed_percentile_returns_none_below_min_sample_size(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    for i in range(5):
        store.insert_observations(
            [_obs("crypto_fear_greed", float(i * 10), f"2026-07-0{i+1}T00:00:00+00:00", unit="index_0_100")]
        )
    calculator = FearGreedPercentileCalculator()

    assert calculator.compute(store, now=_NOW) is None


def test_fear_greed_percentile_computes_rank_of_latest_value(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    for i in range(10):
        store.insert_observations(
            [_obs("crypto_fear_greed", float(i * 10), f"2026-07-{i+1:02d}T00:00:00+00:00", unit="index_0_100")]
        )
    calculator = FearGreedPercentileCalculator()

    observation = calculator.compute(store, now=_NOW)

    assert observation is not None
    assert observation.value == 100.0
    assert observation.metric_id == "crypto_fear_greed_percentile"


def test_rolling_average_returns_none_without_any_observation_in_window(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [_obs("crypto_fear_greed", 50.0, "2026-06-01T00:00:00+00:00", unit="index_0_100")]
    )
    calculator = RollingAverageCalculator(
        source_metric_id="crypto_fear_greed", metric_id="crypto_fear_greed_avg_7d", window_days=7, unit="index_0_100"
    )

    assert calculator.compute(store, now=_NOW) is None


def test_rolling_average_computes_mean_within_window(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [
            _obs("crypto_fear_greed", 20.0, "2026-07-08T00:00:00+00:00", unit="index_0_100"),
            _obs("crypto_fear_greed", 40.0, "2026-07-10T00:00:00+00:00", unit="index_0_100"),
            _obs("crypto_fear_greed", 60.0, "2026-07-12T00:00:00+00:00", unit="index_0_100"),
            # outside the 7d window from _NOW (2026-07-12) -- must not be counted
            _obs("crypto_fear_greed", 0.0, "2026-06-01T00:00:00+00:00", unit="index_0_100"),
        ]
    )
    calculator = RollingAverageCalculator(
        source_metric_id="crypto_fear_greed", metric_id="crypto_fear_greed_avg_7d", window_days=7, unit="index_0_100"
    )

    observation = calculator.compute(store, now=_NOW)

    assert observation is not None
    assert observation.value == 40.0
    assert observation.metadata["sample_size"] == 3


def test_days_since_extreme_returns_none_without_any_extreme(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [_obs("crypto_fear_greed", 50.0, "2026-07-01T00:00:00+00:00", unit="index_0_100")]
    )
    calculator = DaysSinceFearGreedExtremeCalculator()

    assert calculator.compute(store, now=_NOW) is None


def test_days_since_extreme_computes_days_since_latest_extreme(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [
            _obs("crypto_fear_greed", 15.0, "2026-07-02T00:00:00+00:00", unit="index_0_100"),  # extreme fear
            _obs("crypto_fear_greed", 50.0, "2026-07-10T00:00:00+00:00", unit="index_0_100"),  # not extreme
        ]
    )
    calculator = DaysSinceFearGreedExtremeCalculator()

    observation = calculator.compute(store, now=_NOW)

    assert observation is not None
    assert observation.value == 10.0
    assert observation.metadata["extreme_value"] == 15.0


def test_days_since_extreme_counts_extreme_greed_too(tmp_path):
    store = MetricStore(str(tmp_path / "trades.db"))
    store.insert_observations(
        [_obs("crypto_fear_greed", 85.0, "2026-07-11T00:00:00+00:00", unit="index_0_100")]
    )
    calculator = DaysSinceFearGreedExtremeCalculator()

    observation = calculator.compute(store, now=_NOW)

    assert observation is not None
    assert observation.value == 1.0
