"""Global crypto market totals/dominance provider (CoinGecko)."""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from src.market_intelligence.models import MetricObservation
from src.market_intelligence.providers.base import MarketDataProvider, coerce_float, request_with_retry

_URL = "https://api.coingecko.com/api/v3/global"
_METHODOLOGY_VERSION = "coingecko_global_v1"


class CoinGeckoGlobalProvider(MarketDataProvider):
    provider_id = "coingecko_global"
    min_refresh_interval_seconds = 300.0
    metric_ids = (
        "global_market_cap_usd",
        "global_volume_24h_usd",
        "btc_dominance_pct",
        "eth_dominance_pct",
    )

    def fetch_observations(self) -> list[MetricObservation]:
        def _fetch() -> dict:
            resp = requests.get(_URL, timeout=5)
            resp.raise_for_status()
            return resp.json()

        payload = request_with_retry(_fetch)
        data = (payload or {}).get("data") or {}

        updated_at_raw = data.get("updated_at")
        try:
            observed_at = datetime.fromtimestamp(int(updated_at_raw), tz=timezone.utc).isoformat()
        except (TypeError, ValueError):
            observed_at = datetime.now(timezone.utc).isoformat()
        fetched_at = datetime.now(timezone.utc).isoformat()

        dominance = data.get("market_cap_percentage") or {}
        values: dict[str, tuple[float | None, str]] = {
            "global_market_cap_usd": (coerce_float((data.get("total_market_cap") or {}).get("usd")), "usd"),
            "global_volume_24h_usd": (coerce_float((data.get("total_volume") or {}).get("usd")), "usd"),
            "btc_dominance_pct": (coerce_float(dominance.get("btc")), "pct"),
            "eth_dominance_pct": (coerce_float(dominance.get("eth")), "pct"),
        }

        observations: list[MetricObservation] = []
        for metric_id, (value, unit) in values.items():
            if value is None:
                continue
            observations.append(
                MetricObservation(
                    metric_id=metric_id,
                    observed_at=observed_at,
                    value=value,
                    unit=unit,
                    source_provider=self.provider_id,
                    source_reference=_URL,
                    fetched_at=fetched_at,
                    quality="raw",
                    is_estimated=False,
                    methodology_version=_METHODOLOGY_VERSION,
                    metadata={},
                )
            )
        if not observations:
            raise ValueError("CoinGecko global market payload had no usable values")
        return observations
