"""Pillar-level regime assessment (plan.md Phase 4).

A structured interpretation of already-persisted evidence, not a trading
signal and not a composite score (plan.md 5.5 explicitly starts with a
regime *map* across pillars, not one headline number).

Assessments are computed on demand from persisted observations bounded by
``at`` — nothing is written to a new table. This makes any historical
timestamp T trivially and exactly reproducible (replay just calls
``assess_all`` again with a different ``at``), and guarantees replay can
never see an observation recorded after T, since every read goes through
``MetricStore.latest_at_or_before``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.market_intelligence import registry
from src.market_intelligence.freshness import compute_freshness
from src.market_intelligence.models import RegimeAssessment
from src.market_intelligence.regime import rules
from src.market_intelligence.storage.metric_store import MetricStore

_FRESHNESS_CONFIDENCE = {
    "fresh": 1.0,
    "stale": 0.6,
    "very_stale": 0.3,
    "unavailable": 0.0,
}

_METHODOLOGY_VERSION = "market_regime_v1"

# Pillars with no registered metric to assess yet (see docs/market-intelligence.md
# catalog): honestly unavailable rather than guessed, same principle already
# applied to the holder_behavior pillar's metric tiles since Phase 2.
_UNASSESSED_PILLARS = {
    "holder_behavior": (
        "No holder-behavior metric registered yet "
        "(blocked on Coin Metrics Community catalog confirmation)."
    ),
}


def _evidence_for(store: MetricStore, metric_id: str, *, at: datetime) -> tuple[float, dict[str, Any] | None]:
    """Confidence + evidence entry for one input metric, or (0.0, None) if absent."""
    definition = registry.get_definition(metric_id)
    observation = store.latest_at_or_before(metric_id, at)
    if definition is None or observation is None:
        return 0.0, None
    freshness = compute_freshness(observation.observed_at, definition.freshness_ttl_seconds, now=at)
    confidence = _FRESHNESS_CONFIDENCE.get(freshness, 0.0)
    evidence = {
        "metric_id": metric_id,
        "value": observation.value,
        "observed_at": observation.observed_at,
        "freshness": freshness,
    }
    return confidence, evidence


def _build(pillar: str, state: str, confidence: float, summary: str, evidence: list[dict[str, Any] | None], *, at: datetime) -> RegimeAssessment:
    calculated_at = at.isoformat()
    return RegimeAssessment(
        pillar=pillar,
        state=state,
        confidence=round(confidence if state != "unavailable" else 0.0, 4),
        summary=summary,
        evidence=[entry for entry in evidence if entry is not None],
        calculated_at=calculated_at,
        valid_until=calculated_at,  # a fresh on-demand read, not a cached TTL window
        methodology_version=_METHODOLOGY_VERSION,
    )


def assess_derivatives(store: MetricStore, *, at: datetime) -> RegimeAssessment:
    confidence, evidence = _evidence_for(store, "okx_funding_annualized", at=at)
    observation = store.latest_at_or_before("okx_funding_annualized", at)
    state, summary = rules.classify_derivatives(observation.value if observation else None)

    regime_obs = store.latest_at_or_before("okx_price_oi_regime", at)
    regime_evidence = (
        {
            "metric_id": "okx_price_oi_regime",
            "value": regime_obs.value,
            "observed_at": regime_obs.observed_at,
            "label": regime_obs.metadata.get("label"),
        }
        if regime_obs is not None
        else None
    )
    return _build("derivatives", state, confidence, summary, [evidence, regime_evidence], at=at)


def assess_price_breadth(store: MetricStore, *, at: datetime) -> RegimeAssessment:
    confidence, evidence = _evidence_for(store, "market_breadth_advancing_pct", at=at)
    observation = store.latest_at_or_before("market_breadth_advancing_pct", at)
    state, summary = rules.classify_price_breadth(observation.value if observation else None)
    return _build("price_breadth", state, confidence, summary, [evidence], at=at)


def assess_valuation(store: MetricStore, *, at: datetime) -> RegimeAssessment:
    confidence, evidence = _evidence_for(store, "btc_mvrv_z", at=at)
    observation = store.latest_at_or_before("btc_mvrv_z", at)
    state, summary = rules.classify_valuation(observation.value if observation else None)

    percentile_obs = store.latest_at_or_before("btc_mvrv_percentile", at)
    percentile_evidence = (
        {
            "metric_id": "btc_mvrv_percentile",
            "value": percentile_obs.value,
            "observed_at": percentile_obs.observed_at,
        }
        if percentile_obs is not None
        else None
    )
    return _build("valuation", state, confidence, summary, [evidence, percentile_evidence], at=at)


def assess_liquidity(store: MetricStore, *, at: datetime) -> RegimeAssessment:
    confidence, evidence = _evidence_for(store, "stablecoin_mcap_change_7d_pct", at=at)
    observation = store.latest_at_or_before("stablecoin_mcap_change_7d_pct", at)
    state, summary = rules.classify_liquidity(observation.value if observation else None)
    return _build("liquidity", state, confidence, summary, [evidence], at=at)


def assess_sentiment(store: MetricStore, *, at: datetime) -> RegimeAssessment:
    confidence, evidence = _evidence_for(store, "crypto_fear_greed", at=at)
    observation = store.latest_at_or_before("crypto_fear_greed", at)
    state, summary = rules.classify_sentiment(observation.value if observation else None)

    percentile_obs = store.latest_at_or_before("crypto_fear_greed_percentile", at)
    percentile_evidence = (
        {
            "metric_id": "crypto_fear_greed_percentile",
            "value": percentile_obs.value,
            "observed_at": percentile_obs.observed_at,
        }
        if percentile_obs is not None
        else None
    )
    return _build("sentiment", state, confidence, summary, [evidence, percentile_evidence], at=at)


def _unassessed(pillar: str, reason: str, *, at: datetime) -> RegimeAssessment:
    return _build(pillar, "unavailable", 0.0, reason, [], at=at)


def assess_all(store: MetricStore, *, at: datetime | None = None) -> list[RegimeAssessment]:
    resolved_at = at or datetime.now(timezone.utc)
    if resolved_at.tzinfo is None:
        resolved_at = resolved_at.replace(tzinfo=timezone.utc)
    return [
        assess_derivatives(store, at=resolved_at),
        assess_valuation(store, at=resolved_at),
        assess_price_breadth(store, at=resolved_at),
        _unassessed("holder_behavior", _UNASSESSED_PILLARS["holder_behavior"], at=resolved_at),
        assess_liquidity(store, at=resolved_at),
        assess_sentiment(store, at=resolved_at),
    ]
