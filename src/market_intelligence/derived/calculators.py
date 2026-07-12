"""Derived-evidence calculators (plan.md Phase 3): funding annualization, MVRV
percentile within Maybech's own persisted history, stablecoin mcap change, an
OKX price/open-interest quadrant classification, and Fear & Greed rolling
context (7d/30d average, percentile, days since the last extreme reading).

Every calculator reads only already-persisted ``market_metric_observations``
rows written by existing providers — none of them call an external API. Each
returns ``None`` when its inputs are insufficient so the metric stays
``unavailable`` in the UI instead of showing a fabricated or misleading value.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.market_intelligence.derived.base import DerivedCalculator
from src.market_intelligence.models import MetricObservation
from src.market_intelligence.storage.metric_store import MetricStore

_SOURCE_PROVIDER = "maybech_derived"


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _closest_at_or_before(history: list[MetricObservation], cutoff: datetime) -> MetricObservation | None:
    """``history`` must be ascending by observed_at (as MetricStore.history returns it)."""
    candidate: MetricObservation | None = None
    for obs in history:
        if _parse_iso(obs.observed_at) <= cutoff:
            candidate = obs
        else:
            break
    return candidate


class FundingAnnualizedCalculator(DerivedCalculator):
    """Annualizes the OI-weighted OKX funding rate, assuming OKX's 8h funding interval."""

    metric_id = "okx_funding_annualized"
    min_refresh_interval_seconds = 60.0
    _SOURCE_METRIC_ID = "okx_oi_weighted_funding"
    _FUNDINGS_PER_YEAR = 3 * 365

    def compute(self, store: MetricStore, *, now: datetime) -> MetricObservation | None:
        base = store.latest(self._SOURCE_METRIC_ID)
        if base is None:
            return None
        observed_at = now.isoformat()
        return MetricObservation(
            metric_id=self.metric_id,
            observed_at=observed_at,
            value=base.value * self._FUNDINGS_PER_YEAR,
            unit="rate",
            source_provider=_SOURCE_PROVIDER,
            source_reference=f"{self._SOURCE_METRIC_ID} * {self._FUNDINGS_PER_YEAR} (OKX 8h funding interval)",
            fetched_at=observed_at,
            quality="derived",
            is_estimated=False,
            methodology_version="okx_funding_annualized_v1",
            metadata={"base_observed_at": base.observed_at},
        )


def _percentile_observation(
    store: MetricStore,
    *,
    source_metric_id: str,
    metric_id: str,
    methodology_version: str,
    now: datetime,
    min_sample_size: int,
) -> MetricObservation | None:
    """Percentile rank of the latest ``source_metric_id`` value within Maybech's own persisted history.

    Not a full-cycle percentile (that would need years of backfilled history
    this system does not have) — the caveat lives on the registry entry.
    """
    history = store.history(source_metric_id, limit=2000)
    if len(history) < min_sample_size:
        return None
    latest_value = history[-1].value
    values = sorted(obs.value for obs in history)
    rank = sum(1 for value in values if value <= latest_value)
    percentile = (rank / len(values)) * 100.0
    observed_at = now.isoformat()
    return MetricObservation(
        metric_id=metric_id,
        observed_at=observed_at,
        value=percentile,
        unit="pct",
        source_provider=_SOURCE_PROVIDER,
        source_reference=(
            f"percentile rank of latest {source_metric_id} within {len(values)} persisted observations"
        ),
        fetched_at=observed_at,
        quality="derived",
        is_estimated=False,
        methodology_version=methodology_version,
        metadata={"sample_size": len(values), "source_observed_at": history[-1].observed_at},
    )


class MvrvPercentileCalculator(DerivedCalculator):
    """Percentile rank of the latest BTC MVRV Z-Score within Maybech's own persisted history."""

    metric_id = "btc_mvrv_percentile"
    min_refresh_interval_seconds = 3600.0
    _SOURCE_METRIC_ID = "btc_mvrv_z"
    _MIN_SAMPLE_SIZE = 10

    def compute(self, store: MetricStore, *, now: datetime) -> MetricObservation | None:
        return _percentile_observation(
            store,
            source_metric_id=self._SOURCE_METRIC_ID,
            metric_id=self.metric_id,
            methodology_version="btc_mvrv_percentile_v1",
            now=now,
            min_sample_size=self._MIN_SAMPLE_SIZE,
        )


class FearGreedPercentileCalculator(DerivedCalculator):
    """Percentile rank of the latest Fear & Greed reading within Maybech's own persisted history."""

    metric_id = "crypto_fear_greed_percentile"
    min_refresh_interval_seconds = 3600.0
    _SOURCE_METRIC_ID = "crypto_fear_greed"
    _MIN_SAMPLE_SIZE = 10

    def compute(self, store: MetricStore, *, now: datetime) -> MetricObservation | None:
        return _percentile_observation(
            store,
            source_metric_id=self._SOURCE_METRIC_ID,
            metric_id=self.metric_id,
            methodology_version="crypto_fear_greed_percentile_v1",
            now=now,
            min_sample_size=self._MIN_SAMPLE_SIZE,
        )


class RollingAverageCalculator(DerivedCalculator):
    """Mean of a source metric's observations within a trailing window."""

    def __init__(
        self,
        *,
        source_metric_id: str,
        metric_id: str,
        window_days: int,
        unit: str,
        min_refresh_interval_seconds: float = 1800.0,
    ) -> None:
        self._source_metric_id = source_metric_id
        self.metric_id = metric_id
        self.window_days = window_days
        self._unit = unit
        self.min_refresh_interval_seconds = min_refresh_interval_seconds

    def compute(self, store: MetricStore, *, now: datetime) -> MetricObservation | None:
        window_start = (now - timedelta(days=self.window_days)).isoformat()
        history = store.history(self._source_metric_id, start=window_start, limit=2000)
        if not history:
            return None
        average = sum(obs.value for obs in history) / len(history)
        observed_at = now.isoformat()
        return MetricObservation(
            metric_id=self.metric_id,
            observed_at=observed_at,
            value=average,
            unit=self._unit,
            source_provider=_SOURCE_PROVIDER,
            source_reference=f"mean of {self._source_metric_id} observations in the trailing {self.window_days}d",
            fetched_at=observed_at,
            quality="derived",
            is_estimated=False,
            methodology_version=f"{self.metric_id}_v1",
            metadata={"sample_size": len(history), "window_days": self.window_days},
        )


class DaysSinceFearGreedExtremeCalculator(DerivedCalculator):
    """Days since the most recent extreme-fear or extreme-greed reading in persisted history."""

    metric_id = "days_since_fear_greed_extreme"
    min_refresh_interval_seconds = 3600.0
    _SOURCE_METRIC_ID = "crypto_fear_greed"
    _EXTREME_FEAR_MAX = 20.0
    _EXTREME_GREED_MIN = 80.0

    def compute(self, store: MetricStore, *, now: datetime) -> MetricObservation | None:
        history = store.history(self._SOURCE_METRIC_ID, limit=2000)
        extremes = [
            obs for obs in history if obs.value <= self._EXTREME_FEAR_MAX or obs.value >= self._EXTREME_GREED_MIN
        ]
        if not extremes:
            return None
        most_recent = max(extremes, key=lambda obs: obs.observed_at)
        days = max(0.0, (now - _parse_iso(most_recent.observed_at)).total_seconds() / 86400.0)
        observed_at = now.isoformat()
        return MetricObservation(
            metric_id=self.metric_id,
            observed_at=observed_at,
            value=days,
            unit="days",
            source_provider=_SOURCE_PROVIDER,
            source_reference=(
                f"days since latest {self._SOURCE_METRIC_ID} observation "
                f"<= {self._EXTREME_FEAR_MAX:.0f} or >= {self._EXTREME_GREED_MIN:.0f}"
            ),
            fetched_at=observed_at,
            quality="derived",
            is_estimated=False,
            methodology_version="days_since_fear_greed_extreme_v1",
            metadata={"extreme_value": most_recent.value, "extreme_observed_at": most_recent.observed_at},
        )


class PercentChangeCalculator(DerivedCalculator):
    """Percent change of a source metric vs. the closest observation at/before a lookback window."""

    def __init__(
        self,
        *,
        source_metric_id: str,
        metric_id: str,
        window_days: int,
        min_refresh_interval_seconds: float = 1800.0,
    ) -> None:
        self._source_metric_id = source_metric_id
        self.metric_id = metric_id
        self.window_days = window_days
        self.min_refresh_interval_seconds = min_refresh_interval_seconds

    def compute(self, store: MetricStore, *, now: datetime) -> MetricObservation | None:
        history = store.history(self._source_metric_id, limit=2000)
        if len(history) < 2:
            return None
        latest = history[-1]
        cutoff = now - timedelta(days=self.window_days)
        reference = _closest_at_or_before(history, cutoff)
        if reference is None or reference.value == 0:
            return None
        change_pct = (latest.value - reference.value) / reference.value * 100.0
        observed_at = now.isoformat()
        return MetricObservation(
            metric_id=self.metric_id,
            observed_at=observed_at,
            value=change_pct,
            unit="pct",
            source_provider=_SOURCE_PROVIDER,
            source_reference=f"{self._source_metric_id} change vs closest observation at/before {cutoff.isoformat()}",
            fetched_at=observed_at,
            quality="derived",
            is_estimated=False,
            methodology_version=f"{self.metric_id}_v1",
            metadata={"reference_observed_at": reference.observed_at, "reference_value": reference.value},
        )


PRICE_OI_REGIME_LABELS = {
    0: "flat / no clear regime",
    1: "new longs (bullish continuation)",
    2: "short covering",
    3: "new shorts (bearish continuation)",
    4: "long liquidation",
}


class PriceOiRegimeCalculator(DerivedCalculator):
    """Classifies BTC price/open-interest movement into a standard four-quadrant regime.

    A well-documented derivatives-analysis convention (price up/OI up = new
    longs, price up/OI down = short covering, price down/OI up = new shorts,
    price down/OI down = long liquidation) — not an invented composite score,
    and distinct from the (not-yet-built) Phase 4 regime-assessment engine.
    """

    metric_id = "okx_price_oi_regime"
    min_refresh_interval_seconds = 300.0
    _PRICE_METRIC_ID = "okx_btc_price_usd"
    _OI_METRIC_ID = "okx_btc_oi_usd"
    _LOOKBACK_HOURS = 24
    _DEADBAND_PCT = 0.5

    def compute(self, store: MetricStore, *, now: datetime) -> MetricObservation | None:
        price_history = store.history(self._PRICE_METRIC_ID, limit=2000)
        oi_history = store.history(self._OI_METRIC_ID, limit=2000)
        if not price_history or not oi_history:
            return None
        latest_price = price_history[-1]
        latest_oi = oi_history[-1]
        cutoff = now - timedelta(hours=self._LOOKBACK_HOURS)
        ref_price = _closest_at_or_before(price_history, cutoff)
        ref_oi = _closest_at_or_before(oi_history, cutoff)
        if ref_price is None or ref_oi is None or ref_price.value == 0 or ref_oi.value == 0:
            return None
        price_change_pct = (latest_price.value - ref_price.value) / ref_price.value * 100.0
        oi_change_pct = (latest_oi.value - ref_oi.value) / ref_oi.value * 100.0
        code = self._classify(price_change_pct, oi_change_pct)
        observed_at = now.isoformat()
        return MetricObservation(
            metric_id=self.metric_id,
            observed_at=observed_at,
            value=float(code),
            unit="regime_code",
            source_provider=_SOURCE_PROVIDER,
            source_reference=f"{self._PRICE_METRIC_ID} + {self._OI_METRIC_ID} over {self._LOOKBACK_HOURS}h",
            fetched_at=observed_at,
            quality="derived",
            is_estimated=False,
            methodology_version="okx_price_oi_regime_v1",
            metadata={
                "label": PRICE_OI_REGIME_LABELS[code],
                "price_change_pct": round(price_change_pct, 2),
                "oi_change_pct": round(oi_change_pct, 2),
                "lookback_hours": self._LOOKBACK_HOURS,
            },
        )

    def _classify(self, price_change_pct: float, oi_change_pct: float) -> int:
        price_up = price_change_pct >= self._DEADBAND_PCT
        price_down = price_change_pct <= -self._DEADBAND_PCT
        oi_up = oi_change_pct >= self._DEADBAND_PCT
        oi_down = oi_change_pct <= -self._DEADBAND_PCT
        if price_up and oi_up:
            return 1
        if price_up and oi_down:
            return 2
        if price_down and oi_up:
            return 3
        if price_down and oi_down:
            return 4
        return 0


def default_calculators() -> list[DerivedCalculator]:
    return [
        FundingAnnualizedCalculator(),
        MvrvPercentileCalculator(),
        PercentChangeCalculator(
            source_metric_id="stablecoin_total_mcap_usd",
            metric_id="stablecoin_mcap_change_7d_pct",
            window_days=7,
        ),
        PercentChangeCalculator(
            source_metric_id="stablecoin_total_mcap_usd",
            metric_id="stablecoin_mcap_change_30d_pct",
            window_days=30,
        ),
        PriceOiRegimeCalculator(),
        RollingAverageCalculator(
            source_metric_id="crypto_fear_greed",
            metric_id="crypto_fear_greed_avg_7d",
            window_days=7,
            unit="index_0_100",
        ),
        RollingAverageCalculator(
            source_metric_id="crypto_fear_greed",
            metric_id="crypto_fear_greed_avg_30d",
            window_days=30,
            unit="index_0_100",
        ),
        FearGreedPercentileCalculator(),
        DaysSinceFearGreedExtremeCalculator(),
    ]
