"""OKX-scoped price/funding/open-interest provider (read-only market data).

Reuses src/market/macro_overview.py's already-tested fetch_prices and
fetch_funding_overview instead of re-implementing OKX ticker/funding/OI
parsing — those functions already degrade one bad symbol without failing the
whole call, which this provider inherits.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.market.macro_overview import FUNDING_SYMBOLS, fetch_funding_overview, fetch_prices
from src.market_intelligence.models import MetricObservation
from src.market_intelligence.providers.base import MarketDataProvider, ProviderError

_PRICE_METRIC_BY_SYMBOL = {
    "BTC-USDT-SWAP": "okx_btc_price_usd",
    "ETH-USDT-SWAP": "okx_eth_price_usd",
}
_FUNDING_METRIC_BY_SYMBOL = {
    "BTC-USDT-SWAP": "okx_btc_funding_rate",
    "ETH-USDT-SWAP": "okx_eth_funding_rate",
}
_OI_METRIC_BY_SYMBOL = {
    "BTC-USDT-SWAP": "okx_btc_oi_usd",
    "ETH-USDT-SWAP": "okx_eth_oi_usd",
}
_WEIGHTED_FUNDING_METRIC_ID = "okx_oi_weighted_funding"


class OKXMarketProvider(MarketDataProvider):
    provider_id = "okx_market"
    min_refresh_interval_seconds = 60.0
    metric_ids = (
        "okx_btc_price_usd",
        "okx_eth_price_usd",
        "okx_btc_funding_rate",
        "okx_eth_funding_rate",
        "okx_btc_oi_usd",
        "okx_eth_oi_usd",
        _WEIGHTED_FUNDING_METRIC_ID,
    )

    def __init__(self, client: Any | None, *, symbols: tuple[str, ...] = FUNDING_SYMBOLS) -> None:
        self.client = client
        self.symbols = symbols

    def is_configured(self) -> bool:
        return self.client is not None

    def fetch_observations(self) -> list[MetricObservation]:
        if self.client is None:
            raise ProviderError(
                "OKX exchange client not configured for this process (simulation mode has no live feed)",
                category="not_configured",
            )

        fetched_at = datetime.now(timezone.utc).isoformat()
        # OKX ticker/funding/OI are current-snapshot reads with no historical
        # timestamp of their own, so the fetch time is the observation time.
        observed_at = fetched_at
        observations: list[MetricObservation] = []

        for row in fetch_prices(self.client, symbols=self.symbols):
            metric_id = _PRICE_METRIC_BY_SYMBOL.get(row["symbol"])
            if metric_id is None or row["last_price"] is None:
                continue
            observations.append(
                MetricObservation(
                    metric_id=metric_id,
                    observed_at=observed_at,
                    value=float(row["last_price"]),
                    unit="usd",
                    source_provider=self.provider_id,
                    source_reference="okx_ticker",
                    fetched_at=fetched_at,
                    quality="raw",
                    is_estimated=False,
                    methodology_version="okx_ticker_v1",
                    metadata={},
                )
            )

        funding = fetch_funding_overview(self.client, symbols=self.symbols)
        for entry in funding.get("entries", []):
            symbol = entry.get("symbol")
            if entry.get("funding_rate") is not None:
                metric_id = _FUNDING_METRIC_BY_SYMBOL.get(symbol)
                if metric_id is not None:
                    observations.append(
                        MetricObservation(
                            metric_id=metric_id,
                            observed_at=observed_at,
                            value=float(entry["funding_rate"]),
                            unit="rate",
                            source_provider=self.provider_id,
                            source_reference="okx_funding_rate",
                            fetched_at=fetched_at,
                            quality="raw",
                            is_estimated=False,
                            methodology_version="okx_funding_v1",
                            metadata={},
                        )
                    )
            if entry.get("open_interest_ccy") is not None:
                metric_id = _OI_METRIC_BY_SYMBOL.get(symbol)
                if metric_id is not None:
                    observations.append(
                        MetricObservation(
                            metric_id=metric_id,
                            observed_at=observed_at,
                            value=float(entry["open_interest_ccy"]),
                            unit="usd",
                            source_provider=self.provider_id,
                            source_reference="okx_open_interest",
                            fetched_at=fetched_at,
                            quality="raw",
                            is_estimated=False,
                            methodology_version="okx_open_interest_v1",
                            metadata={},
                        )
                    )

        weighted_rate = funding.get("weighted_average_funding_rate")
        if weighted_rate is not None:
            observations.append(
                MetricObservation(
                    metric_id=_WEIGHTED_FUNDING_METRIC_ID,
                    observed_at=observed_at,
                    value=float(weighted_rate),
                    unit="rate",
                    source_provider=self.provider_id,
                    source_reference="okx_funding_rate+okx_open_interest",
                    fetched_at=fetched_at,
                    quality="derived",
                    is_estimated=False,
                    methodology_version="okx_oi_weighted_funding_v1",
                    metadata={},
                )
            )

        if not observations:
            raise ValueError("OKX market provider returned no usable observations")
        return observations
