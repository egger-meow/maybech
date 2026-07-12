"""Static metric registry seeded from the docs/market-intelligence.md catalog.

Only metrics with a live, wired provider are registered here. Planned metrics
from the catalog (open interest, holder-behavior proxies, DefiLlama liquidity,
derived evidence) are added in later phases as their providers are verified —
an unregistered metric_id is reported `unavailable` by the service rather than
guessed.
"""

from __future__ import annotations

from src.market_intelligence.models import MetricDefinition

_DEFINITIONS: dict[str, MetricDefinition] = {
    definition.metric_id: definition
    for definition in [
        MetricDefinition(
            metric_id="crypto_fear_greed",
            name="Fear & Greed Index",
            pillar="sentiment",
            description="Crowd sentiment index for crypto markets.",
            unit="index_0_100",
            scope="market_wide",
            source_kind="raw",
            primary_provider="alternative_me",
            provider_metric_or_endpoint="https://api.alternative.me/fng/",
            expected_frequency_seconds=86400,
            freshness_ttl_seconds=86400 * 2,
            history_support=True,
            methodology_version="alternative_me_v1",
            caveats=(
                "Updates roughly once daily; not a second-level feed. "
                "Attribution to alternative.me is required wherever displayed."
            ),
        ),
        MetricDefinition(
            metric_id="btc_mvrv_z",
            name="BTC MVRV Z-Score",
            pillar="valuation",
            description="Market-value-to-realized-value Z-score for Bitcoin.",
            unit="z_score",
            scope="btc",
            source_kind="raw",
            primary_provider="bitcoin_data_mvrv",
            provider_metric_or_endpoint="https://bitcoin-data.com/v1/mvrv-zscore",
            expected_frequency_seconds=86400,
            freshness_ttl_seconds=86400 * 2,
            history_support=True,
            methodology_version="bgeometrics_mvrv_zscore_v1",
            caveats=(
                "Provider-computed Z-score (BGeometrics/bitcoin-data.com "
                "methodology), not a Maybech-derived score. Long-horizon "
                "valuation context only, not a timing signal."
            ),
        ),
        MetricDefinition(
            metric_id="global_market_cap_usd",
            name="Global Crypto Market Cap",
            pillar="price_breadth",
            description="Total market capitalization across all tracked crypto assets.",
            unit="usd",
            scope="market_wide",
            source_kind="raw",
            primary_provider="coingecko_global",
            provider_metric_or_endpoint="https://api.coingecko.com/api/v3/global",
            expected_frequency_seconds=300,
            freshness_ttl_seconds=900,
            history_support=False,
            methodology_version="coingecko_global_v1",
            caveats="CoinGecko's own aggregation methodology; only accumulates history going forward, no backfill endpoint wired yet.",
        ),
        MetricDefinition(
            metric_id="global_volume_24h_usd",
            name="Global 24h Volume",
            pillar="price_breadth",
            description="Total 24-hour trading volume across all tracked crypto assets.",
            unit="usd",
            scope="market_wide",
            source_kind="raw",
            primary_provider="coingecko_global",
            provider_metric_or_endpoint="https://api.coingecko.com/api/v3/global",
            expected_frequency_seconds=300,
            freshness_ttl_seconds=900,
            history_support=False,
            methodology_version="coingecko_global_v1",
            caveats="CoinGecko's own aggregation methodology; only accumulates history going forward, no backfill endpoint wired yet.",
        ),
        MetricDefinition(
            metric_id="btc_dominance_pct",
            name="BTC Dominance",
            pillar="price_breadth",
            description="Bitcoin's share of total crypto market capitalization.",
            unit="pct",
            scope="market_wide",
            source_kind="raw",
            primary_provider="coingecko_global",
            provider_metric_or_endpoint="https://api.coingecko.com/api/v3/global",
            expected_frequency_seconds=300,
            freshness_ttl_seconds=900,
            history_support=False,
            methodology_version="coingecko_global_v1",
            caveats="CoinGecko's own aggregation methodology; only accumulates history going forward, no backfill endpoint wired yet.",
        ),
        MetricDefinition(
            metric_id="eth_dominance_pct",
            name="ETH Dominance",
            pillar="price_breadth",
            description="Ethereum's share of total crypto market capitalization.",
            unit="pct",
            scope="market_wide",
            source_kind="raw",
            primary_provider="coingecko_global",
            provider_metric_or_endpoint="https://api.coingecko.com/api/v3/global",
            expected_frequency_seconds=300,
            freshness_ttl_seconds=900,
            history_support=False,
            methodology_version="coingecko_global_v1",
            caveats="CoinGecko's own aggregation methodology; only accumulates history going forward, no backfill endpoint wired yet.",
        ),
    ]
}


def get_definition(metric_id: str) -> MetricDefinition | None:
    return _DEFINITIONS.get(metric_id)


def all_definitions() -> list[MetricDefinition]:
    return list(_DEFINITIONS.values())


def all_pillars() -> list[str]:
    return sorted({definition.pillar for definition in _DEFINITIONS.values()})
