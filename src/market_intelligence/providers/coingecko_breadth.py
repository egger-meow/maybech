"""Market-breadth provider (CoinGecko): % of tracked top-100 coins advancing over 24h.

Separate from ``coingecko.py`` (which wraps ``/api/v3/global``) because this
hits a different endpoint (``/api/v3/coins/markets``) with a different
payload shape; kept as its own provider so one endpoint's failure/rate-limit
never affects the other's sync status.
"""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from src.market_intelligence.models import MetricObservation
from src.market_intelligence.providers.base import MarketDataProvider, request_with_retry

_URL = "https://api.coingecko.com/api/v3/coins/markets"
_METHODOLOGY_VERSION = "coingecko_breadth_top100_v1"
_METRIC_ID = "market_breadth_advancing_pct"
_TRACKED_COUNT = 100


class CoinGeckoBreadthProvider(MarketDataProvider):
    provider_id = "coingecko_breadth"
    min_refresh_interval_seconds = 900.0
    metric_ids = (_METRIC_ID,)

    def fetch_observations(self) -> list[MetricObservation]:
        def _fetch() -> object:
            resp = requests.get(
                _URL,
                params={
                    "vs_currency": "usd",
                    "order": "market_cap_desc",
                    "per_page": _TRACKED_COUNT,
                    "page": 1,
                    "price_change_percentage": "24h",
                },
                timeout=5,
            )
            resp.raise_for_status()
            return resp.json()

        payload = request_with_retry(_fetch)
        if not isinstance(payload, list) or not payload:
            raise ValueError("CoinGecko markets payload was empty or an unexpected shape")

        changes = [
            coin.get("price_change_percentage_24h")
            for coin in payload
            if isinstance(coin, dict) and isinstance(coin.get("price_change_percentage_24h"), (int, float))
        ]
        if not changes:
            raise ValueError("CoinGecko markets payload had no usable price_change_percentage_24h values")

        advancing = sum(1 for change in changes if change > 0)
        advancing_pct = (advancing / len(changes)) * 100.0

        observed_at = datetime.now(timezone.utc).isoformat()
        return [
            MetricObservation(
                metric_id=_METRIC_ID,
                observed_at=observed_at,
                value=advancing_pct,
                unit="pct",
                source_provider=self.provider_id,
                source_reference=_URL,
                fetched_at=observed_at,
                quality="derived",
                is_estimated=False,
                methodology_version=_METHODOLOGY_VERSION,
                metadata={"tracked_count": len(changes), "advancing_count": advancing},
            )
        ]
