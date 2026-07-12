"""BTC MVRV ratio + Z-Score provider (bitcoin-data.com / BGeometrics)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import requests

from src.market_intelligence.models import MetricObservation
from src.market_intelligence.providers.base import MarketDataProvider, coerce_float, request_with_retry

_ZSCORE_URL = "https://bitcoin-data.com/v1/mvrv-zscore"
_RATIO_URL = "https://bitcoin-data.com/v1/mvrv"
_ZSCORE_METRIC_ID = "btc_mvrv_z"
_RATIO_METRIC_ID = "btc_mvrv"

# (url, metric_id, payload value key, unit, methodology_version)
_SERIES = (
    (_ZSCORE_URL, _ZSCORE_METRIC_ID, "mvrvZscore", "z_score", "bgeometrics_mvrv_zscore_v1"),
    (_RATIO_URL, _RATIO_METRIC_ID, "mvrv", "ratio", "bgeometrics_mvrv_ratio_v1"),
)


def _raw_items(payload: Any) -> list[Any] | None:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("value"), list):
        return payload["value"]
    return None


class BitcoinDataMvrvProvider(MarketDataProvider):
    """Fetches both the raw MVRV ratio and the MVRV Z-Score from the same source.

    Each of the two sibling endpoints is fetched and parsed independently so
    one failing (or returning an unexpected shape) doesn't discard
    observations already obtained from the other.
    """

    provider_id = "bitcoin_data_mvrv"
    min_refresh_interval_seconds = 3600.0
    metric_ids = (_ZSCORE_METRIC_ID, _RATIO_METRIC_ID)

    def __init__(self, *, history_days: int = 180) -> None:
        self.history_days = history_days

    def _fetch_raw_items(self, url: str) -> list[Any]:
        def _fetch() -> Any:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            return resp.json()

        payload = request_with_retry(_fetch)
        raw_items = _raw_items(payload)
        if raw_items is None:
            raise ValueError(f"unexpected payload shape from {url}")
        return raw_items

    def fetch_observations(self) -> list[MetricObservation]:
        fetched_at = datetime.now(timezone.utc).isoformat()
        observations: list[MetricObservation] = []
        errors: list[str] = []

        for url, metric_id, value_key, unit, methodology_version in _SERIES:
            try:
                raw_items = self._fetch_raw_items(url)
            except Exception as exc:  # noqa: BLE001 - one series failing shouldn't drop the other
                errors.append(f"{metric_id}: {exc}")
                continue

            window = raw_items[-self.history_days :] if self.history_days > 0 else raw_items
            for item in window:
                if not isinstance(item, dict):
                    continue
                value = coerce_float(item.get(value_key))
                date = str(item.get("d") or "")
                if value is None or not date:
                    continue
                observations.append(
                    MetricObservation(
                        metric_id=metric_id,
                        observed_at=f"{date}T00:00:00+00:00",
                        value=value,
                        unit=unit,
                        source_provider=self.provider_id,
                        source_reference=url,
                        fetched_at=fetched_at,
                        quality="raw",
                        is_estimated=False,
                        methodology_version=methodology_version,
                        metadata={},
                    )
                )

        if not observations:
            detail = f" ({'; '.join(errors)})" if errors else ""
            raise ValueError(f"bitcoin-data.com returned no usable MVRV observations{detail}")
        return observations
