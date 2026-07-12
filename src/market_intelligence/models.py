"""Domain models for the market intelligence layer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MetricDefinition:
    """A stable registry entry describing what a metric means (plan.md 5.1)."""

    metric_id: str
    name: str
    pillar: str
    description: str
    unit: str
    scope: str
    source_kind: str  # raw | derived | proxy
    primary_provider: str
    provider_metric_or_endpoint: str
    expected_frequency_seconds: int
    freshness_ttl_seconds: int
    history_support: bool
    methodology_version: str
    caveats: str


@dataclass(frozen=True)
class MetricObservation:
    """One timestamped value from a provider (plan.md 5.2)."""

    metric_id: str
    observed_at: str
    value: float
    unit: str
    source_provider: str
    source_reference: str
    fetched_at: str
    quality: str
    is_estimated: bool
    methodology_version: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderSyncRun:
    """One attempted ingestion cycle for one provider."""

    provider_id: str
    started_at: str
    completed_at: str
    status: str  # success | failed | skipped
    records_fetched: int
    records_stored: int
    error_category: str | None = None
    error_detail: str | None = None
