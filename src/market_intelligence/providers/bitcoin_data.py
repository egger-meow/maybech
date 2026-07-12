"""BTC MVRV Z-Score provider (bitcoin-data.com / BGeometrics)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from src.market_intelligence.models import MetricObservation
from src.market_intelligence.providers.base import MarketDataProvider, coerce_float, request_with_retry

_URL = "https://bitcoin-data.com/v1/mvrv-zscore"
_METRIC_ID = "btc_mvrv_z"


def _raw_items(payload: Any) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("value"), list):
        return payload["value"]
    return None


class BitcoinDataMvrvProvider(MarketDataProvider):
    provider_id = "bitcoin_data_mvrv"
    min_refresh_interval_seconds = 3600.0
    metric_ids = (_METRIC_ID,)

    def __init__(self, *, history_days: int = 180) -> None:
        self.history_days = history_days

    def fetch_observations(self) -> list[MetricObservation]:
        def _fetch() -> Any:
            resp = requests.get(_URL, timeout=5)
            resp.raise_for_status()
            return resp.json()

        payload = request_with_retry(_fetch)
        raw_items = _raw_items(payload)
        if raw_items is None:
            raise ValueError("unexpected MVRV Z-Score payload shape")

        fetched_at = datetime.now(timezone.utc).isoformat()
        window = raw_items[-self.history_days :] if self.history_days > 0 else raw_items

        observations: list[MetricObservation] = []
        for item in window:
            if not isinstance(item, dict):
                continue
            value = coerce_float(item.get("mvrvZscore"))
            date = str(item.get("d") or "")
            if value is None or not date:
                continue
            observations.append(
                MetricObservation(
                    metric_id=_METRIC_ID,
                    observed_at=f"{date}T00:00:00+00:00",
                    value=value,
                    unit="z_score",
                    source_provider=self.provider_id,
                    source_reference=_URL,
                    fetched_at=fetched_at,
                    quality="raw",
                    is_estimated=False,
                    methodology_version="bgeometrics_mvrv_zscore_v1",
                    metadata={},
                )
            )
        if not observations:
            raise ValueError("bitcoin-data.com returned no usable MVRV Z-Score observations")
        return observations
