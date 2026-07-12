"""Freshness classification: fresh, stale, very_stale, or unavailable."""

from __future__ import annotations

from datetime import datetime, timezone

FRESH = "fresh"
STALE = "stale"
VERY_STALE = "very_stale"
UNAVAILABLE = "unavailable"

# Beyond this multiple of a metric's own TTL, a stale value becomes very_stale
# rather than merely stale. Kept as one shared rule so every metric's meaning
# of "very stale" stays comparable.
_VERY_STALE_MULTIPLIER = 3.0


def compute_freshness(
    observed_at: str | None,
    ttl_seconds: float,
    *,
    now: datetime | None = None,
) -> str:
    if not observed_at:
        return UNAVAILABLE
    try:
        observed = datetime.fromisoformat(observed_at)
    except ValueError:
        return UNAVAILABLE
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    age_seconds = max(0.0, (current - observed).total_seconds())
    if age_seconds <= ttl_seconds:
        return FRESH
    if age_seconds <= ttl_seconds * _VERY_STALE_MULTIPLIER:
        return STALE
    return VERY_STALE
