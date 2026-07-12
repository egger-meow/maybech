"""Fear & Greed Index provider (alternative.me)."""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from src.market_intelligence.models import MetricObservation
from src.market_intelligence.providers.base import MarketDataProvider, request_with_retry

_URL = "https://api.alternative.me/fng/"
_METRIC_ID = "crypto_fear_greed"


class AlternativeMeProvider(MarketDataProvider):
    provider_id = "alternative_me"
    min_refresh_interval_seconds = 300.0
    metric_ids = (_METRIC_ID,)

    def __init__(self, *, history_limit: int = 30) -> None:
        self.history_limit = history_limit

    def fetch_observations(self) -> list[MetricObservation]:
        def _fetch() -> dict:
            resp = requests.get(_URL, params={"limit": self.history_limit, "format": "json"}, timeout=5)
            resp.raise_for_status()
            return resp.json()

        payload = request_with_retry(_fetch)
        items = payload.get("data") or []
        fetched_at = datetime.now(timezone.utc).isoformat()

        observations: list[MetricObservation] = []
        for item in items:
            if not isinstance(item, dict) or "value" not in item:
                continue
            try:
                observed_at = datetime.fromtimestamp(
                    int(item["timestamp"]), tz=timezone.utc
                ).isoformat()
            except (KeyError, TypeError, ValueError):
                continue
            observations.append(
                MetricObservation(
                    metric_id=_METRIC_ID,
                    observed_at=observed_at,
                    value=float(item["value"]),
                    unit="index_0_100",
                    source_provider=self.provider_id,
                    source_reference=_URL,
                    fetched_at=fetched_at,
                    quality="raw",
                    is_estimated=False,
                    methodology_version="alternative_me_v1",
                    metadata={"classification": str(item.get("value_classification") or "")},
                )
            )
        if not observations:
            raise ValueError("alternative.me returned no usable Fear & Greed observations")
        return observations
