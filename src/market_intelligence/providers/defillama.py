"""Total stablecoin market cap provider (DefiLlama, free, no API key)."""

from __future__ import annotations

from datetime import datetime, timezone

import requests

from src.market_intelligence.models import MetricObservation
from src.market_intelligence.providers.base import MarketDataProvider, coerce_float, request_with_retry

_URL = "https://stablecoins.llama.fi/stablecoins"
_METRIC_ID = "stablecoin_total_mcap_usd"


class DefiLlamaStablecoinProvider(MarketDataProvider):
    provider_id = "defillama_stablecoins"
    min_refresh_interval_seconds = 1800.0
    metric_ids = (_METRIC_ID,)

    def fetch_observations(self) -> list[MetricObservation]:
        def _fetch() -> dict:
            resp = requests.get(_URL, params={"includePrices": "false"}, timeout=10)
            resp.raise_for_status()
            return resp.json()

        payload = request_with_retry(_fetch)
        assets = payload.get("peggedAssets") if isinstance(payload, dict) else None
        if not isinstance(assets, list):
            raise ValueError("unexpected DefiLlama stablecoins payload shape")

        total = 0.0
        counted = 0
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            circulating = asset.get("circulating")
            if not isinstance(circulating, dict):
                continue
            value = coerce_float(circulating.get("peggedUSD"))
            if value is None:
                continue
            total += value
            counted += 1
        if counted == 0:
            raise ValueError("DefiLlama stablecoins payload had no usable circulating supply values")

        now = datetime.now(timezone.utc).isoformat()
        return [
            MetricObservation(
                metric_id=_METRIC_ID,
                observed_at=now,
                value=total,
                unit="usd",
                source_provider=self.provider_id,
                source_reference=_URL,
                fetched_at=now,
                quality="raw",
                is_estimated=False,
                methodology_version="defillama_stablecoins_v1",
                metadata={"assets_counted": counted},
            )
        ]
