"""Contract for derived metrics: pure computations over already-persisted history.

Unlike ``providers.base.MarketDataProvider``, a calculator never touches the
network and never raises for "not enough data yet" — it returns ``None`` so
the service can leave the metric honestly ``unavailable`` rather than
fabricating a value (plan.md's regime-layer prohibition applies here too).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from src.market_intelligence.models import MetricObservation
from src.market_intelligence.storage.metric_store import MetricStore


class DerivedCalculator(ABC):
    metric_id: str
    min_refresh_interval_seconds: float = 300.0

    @abstractmethod
    def compute(self, store: MetricStore, *, now: datetime) -> MetricObservation | None:
        """Return a new observation to persist, or None if inputs are insufficient."""
