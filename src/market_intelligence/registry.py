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
            metric_id="btc_mvrv",
            name="BTC MVRV Ratio",
            pillar="valuation",
            description="Market value divided by realized value for Bitcoin.",
            unit="ratio",
            scope="btc",
            source_kind="raw",
            primary_provider="bitcoin_data_mvrv",
            provider_metric_or_endpoint="https://bitcoin-data.com/v1/mvrv",
            expected_frequency_seconds=86400,
            freshness_ttl_seconds=86400 * 2,
            history_support=True,
            methodology_version="bgeometrics_mvrv_ratio_v1",
            caveats=(
                "Provider-computed raw ratio (BGeometrics/bitcoin-data.com "
                "methodology), same source as btc_mvrv_z. Above 1.0 means "
                "aggregate holders are in unrealized profit; below 1.0 means "
                "aggregate unrealized loss. Long-horizon valuation context "
                "only, not a timing signal."
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
        MetricDefinition(
            metric_id="okx_btc_price_usd",
            name="OKX BTC-USDT-SWAP Price",
            pillar="price_breadth",
            description="Last traded price for BTC-USDT-SWAP on OKX.",
            unit="usd",
            scope="okx_btc",
            source_kind="raw",
            primary_provider="okx_market",
            provider_metric_or_endpoint="okx_ticker",
            expected_frequency_seconds=60,
            freshness_ttl_seconds=300,
            history_support=False,
            methodology_version="okx_ticker_v1",
            caveats="OKX-only price, not a cross-exchange index. Unavailable in simulation mode (no live feed).",
        ),
        MetricDefinition(
            metric_id="okx_eth_price_usd",
            name="OKX ETH-USDT-SWAP Price",
            pillar="price_breadth",
            description="Last traded price for ETH-USDT-SWAP on OKX.",
            unit="usd",
            scope="okx_eth",
            source_kind="raw",
            primary_provider="okx_market",
            provider_metric_or_endpoint="okx_ticker",
            expected_frequency_seconds=60,
            freshness_ttl_seconds=300,
            history_support=False,
            methodology_version="okx_ticker_v1",
            caveats="OKX-only price, not a cross-exchange index. Unavailable in simulation mode (no live feed).",
        ),
        MetricDefinition(
            metric_id="okx_btc_funding_rate",
            name="OKX BTC-USDT-SWAP Funding Rate",
            pillar="derivatives",
            description="Current funding rate for BTC-USDT-SWAP on OKX.",
            unit="rate",
            scope="okx_btc",
            source_kind="raw",
            primary_provider="okx_market",
            provider_metric_or_endpoint="okx_funding_rate",
            expected_frequency_seconds=60,
            freshness_ttl_seconds=300,
            history_support=False,
            methodology_version="okx_funding_v1",
            caveats="OKX-only funding, not cross-exchange. Unavailable in simulation mode (no live feed).",
        ),
        MetricDefinition(
            metric_id="okx_eth_funding_rate",
            name="OKX ETH-USDT-SWAP Funding Rate",
            pillar="derivatives",
            description="Current funding rate for ETH-USDT-SWAP on OKX.",
            unit="rate",
            scope="okx_eth",
            source_kind="raw",
            primary_provider="okx_market",
            provider_metric_or_endpoint="okx_funding_rate",
            expected_frequency_seconds=60,
            freshness_ttl_seconds=300,
            history_support=False,
            methodology_version="okx_funding_v1",
            caveats="OKX-only funding, not cross-exchange. Unavailable in simulation mode (no live feed).",
        ),
        MetricDefinition(
            metric_id="okx_btc_oi_usd",
            name="OKX BTC-USDT-SWAP Open Interest",
            pillar="derivatives",
            description="Current open interest (settlement currency) for BTC-USDT-SWAP on OKX.",
            unit="usd",
            scope="okx_btc",
            source_kind="raw",
            primary_provider="okx_market",
            provider_metric_or_endpoint="okx_open_interest",
            expected_frequency_seconds=60,
            freshness_ttl_seconds=300,
            history_support=False,
            methodology_version="okx_open_interest_v1",
            caveats="OKX-only open interest, not cross-exchange. Unavailable in simulation mode (no live feed).",
        ),
        MetricDefinition(
            metric_id="okx_eth_oi_usd",
            name="OKX ETH-USDT-SWAP Open Interest",
            pillar="derivatives",
            description="Current open interest (settlement currency) for ETH-USDT-SWAP on OKX.",
            unit="usd",
            scope="okx_eth",
            source_kind="raw",
            primary_provider="okx_market",
            provider_metric_or_endpoint="okx_open_interest",
            expected_frequency_seconds=60,
            freshness_ttl_seconds=300,
            history_support=False,
            methodology_version="okx_open_interest_v1",
            caveats="OKX-only open interest, not cross-exchange. Unavailable in simulation mode (no live feed).",
        ),
        MetricDefinition(
            metric_id="okx_oi_weighted_funding",
            name="OKX OI-Weighted Funding Rate",
            pillar="derivatives",
            description="Open-interest-weighted average funding rate across BTC/ETH OKX swaps.",
            unit="rate",
            scope="okx",
            source_kind="derived",
            primary_provider="okx_market",
            provider_metric_or_endpoint="okx_funding_rate+okx_open_interest",
            expected_frequency_seconds=60,
            freshness_ttl_seconds=300,
            history_support=False,
            methodology_version="okx_oi_weighted_funding_v1",
            caveats="OKX-only weighting scope, not a market-wide derivatives figure. Unavailable in simulation mode.",
        ),
        MetricDefinition(
            metric_id="stablecoin_total_mcap_usd",
            name="Total Stablecoin Market Cap",
            pillar="liquidity",
            description="Sum of circulating USD-pegged stablecoin market capitalization across tracked assets.",
            unit="usd",
            scope="market_wide",
            source_kind="raw",
            primary_provider="defillama_stablecoins",
            provider_metric_or_endpoint="https://stablecoins.llama.fi/stablecoins",
            expected_frequency_seconds=1800,
            freshness_ttl_seconds=7200,
            history_support=False,
            methodology_version="defillama_stablecoins_v1",
            caveats="DefiLlama's own aggregation methodology across pegged-USD assets; only accumulates history going forward.",
        ),
        MetricDefinition(
            metric_id="okx_funding_annualized",
            name="OKX Funding Rate (Annualized)",
            pillar="derivatives",
            description="OI-weighted OKX BTC/ETH funding rate, annualized assuming OKX's 8-hour funding interval.",
            unit="rate",
            scope="okx",
            source_kind="derived",
            primary_provider="maybech_derived",
            provider_metric_or_endpoint="okx_oi_weighted_funding",
            expected_frequency_seconds=60,
            freshness_ttl_seconds=300,
            history_support=False,
            methodology_version="okx_funding_annualized_v1",
            caveats=(
                "Simple annualization (rate x 3 x 365) of the current OI-weighted funding "
                "rate; assumes today's rate holds for a year, which it won't. OKX-only "
                "scope, not a market-wide figure. Unavailable in simulation mode."
            ),
        ),
        MetricDefinition(
            metric_id="btc_mvrv_percentile",
            name="BTC MVRV Z-Score Percentile",
            pillar="valuation",
            description="Percentile rank of the latest BTC MVRV Z-Score within Maybech's own persisted history.",
            unit="pct",
            scope="btc",
            source_kind="derived",
            primary_provider="maybech_derived",
            provider_metric_or_endpoint="btc_mvrv_z",
            expected_frequency_seconds=3600,
            freshness_ttl_seconds=10800,
            history_support=False,
            methodology_version="btc_mvrv_percentile_v1",
            caveats=(
                "Percentile is computed only against observations Maybech has itself "
                "persisted since ingestion started, not the full multi-year BTC MVRV "
                "cycle history. Grows more meaningful over time; treat early readings "
                "as low-confidence."
            ),
        ),
        MetricDefinition(
            metric_id="stablecoin_mcap_change_7d_pct",
            name="Stablecoin Market Cap Change (7d)",
            pillar="liquidity",
            description="Percent change in total stablecoin market cap vs. ~7 days ago.",
            unit="pct",
            scope="market_wide",
            source_kind="derived",
            primary_provider="maybech_derived",
            provider_metric_or_endpoint="stablecoin_total_mcap_usd",
            expected_frequency_seconds=1800,
            freshness_ttl_seconds=7200,
            history_support=False,
            methodology_version="stablecoin_mcap_change_7d_pct_v1",
            caveats=(
                "Compares against the closest observation Maybech has persisted at or "
                "before the 7-day cutoff; unavailable until at least 7 days of history "
                "have accumulated since ingestion started."
            ),
        ),
        MetricDefinition(
            metric_id="stablecoin_mcap_change_30d_pct",
            name="Stablecoin Market Cap Change (30d)",
            pillar="liquidity",
            description="Percent change in total stablecoin market cap vs. ~30 days ago.",
            unit="pct",
            scope="market_wide",
            source_kind="derived",
            primary_provider="maybech_derived",
            provider_metric_or_endpoint="stablecoin_total_mcap_usd",
            expected_frequency_seconds=1800,
            freshness_ttl_seconds=7200,
            history_support=False,
            methodology_version="stablecoin_mcap_change_30d_pct_v1",
            caveats=(
                "Compares against the closest observation Maybech has persisted at or "
                "before the 30-day cutoff; unavailable until at least 30 days of "
                "history have accumulated since ingestion started."
            ),
        ),
        MetricDefinition(
            metric_id="okx_price_oi_regime",
            name="OKX BTC Price/OI Regime",
            pillar="derivatives",
            description="Four-quadrant classification of BTC price vs. open-interest movement over 24h.",
            unit="regime_code",
            scope="okx_btc",
            source_kind="derived",
            primary_provider="maybech_derived",
            provider_metric_or_endpoint="okx_btc_price_usd+okx_btc_oi_usd",
            expected_frequency_seconds=300,
            freshness_ttl_seconds=900,
            history_support=False,
            methodology_version="okx_price_oi_regime_v1",
            caveats=(
                "Standard price/open-interest quadrant read (new longs, short covering, "
                "new shorts, long liquidation) over a 24h lookback with a 0.5% deadband; "
                "not a composite regime score and not a trading signal. OKX-only scope. "
                "Unavailable in simulation mode or with under 24h of accumulated history."
            ),
        ),
        MetricDefinition(
            metric_id="crypto_fear_greed_avg_7d",
            name="Fear & Greed Index (7d Average)",
            pillar="sentiment",
            description="Mean of Fear & Greed Index observations Maybech has persisted over the trailing 7 days.",
            unit="index_0_100",
            scope="market_wide",
            source_kind="derived",
            primary_provider="maybech_derived",
            provider_metric_or_endpoint="crypto_fear_greed",
            expected_frequency_seconds=1800,
            freshness_ttl_seconds=7200,
            history_support=False,
            methodology_version="crypto_fear_greed_avg_7d_v1",
            caveats=(
                "Averaged only over observations Maybech has itself persisted since "
                "ingestion started; the window is not backfilled from a longer external "
                "history. Sample size shrinks toward the true 7d average as history accumulates."
            ),
        ),
        MetricDefinition(
            metric_id="crypto_fear_greed_avg_30d",
            name="Fear & Greed Index (30d Average)",
            pillar="sentiment",
            description="Mean of Fear & Greed Index observations Maybech has persisted over the trailing 30 days.",
            unit="index_0_100",
            scope="market_wide",
            source_kind="derived",
            primary_provider="maybech_derived",
            provider_metric_or_endpoint="crypto_fear_greed",
            expected_frequency_seconds=1800,
            freshness_ttl_seconds=7200,
            history_support=False,
            methodology_version="crypto_fear_greed_avg_30d_v1",
            caveats=(
                "Averaged only over observations Maybech has itself persisted since "
                "ingestion started; the window is not backfilled from a longer external "
                "history. Sample size shrinks toward the true 30d average as history accumulates."
            ),
        ),
        MetricDefinition(
            metric_id="crypto_fear_greed_percentile",
            name="Fear & Greed Index Percentile",
            pillar="sentiment",
            description="Percentile rank of the latest Fear & Greed reading within Maybech's own persisted history.",
            unit="pct",
            scope="market_wide",
            source_kind="derived",
            primary_provider="maybech_derived",
            provider_metric_or_endpoint="crypto_fear_greed",
            expected_frequency_seconds=3600,
            freshness_ttl_seconds=10800,
            history_support=False,
            methodology_version="crypto_fear_greed_percentile_v1",
            caveats=(
                "Percentile is computed only against observations Maybech has itself "
                "persisted since ingestion started, not a longer external history. "
                "Grows more meaningful over time; treat early readings as low-confidence."
            ),
        ),
        MetricDefinition(
            metric_id="days_since_fear_greed_extreme",
            name="Days Since Fear & Greed Extreme",
            pillar="sentiment",
            description="Days since the most recent Fear & Greed reading <=20 or >=80 in Maybech's persisted history.",
            unit="days",
            scope="market_wide",
            source_kind="derived",
            primary_provider="maybech_derived",
            provider_metric_or_endpoint="crypto_fear_greed",
            expected_frequency_seconds=3600,
            freshness_ttl_seconds=10800,
            history_support=False,
            methodology_version="days_since_fear_greed_extreme_v1",
            caveats=(
                "Only counts extremes Maybech has itself observed since ingestion started; "
                "unavailable until an extreme reading (<=20 or >=80) has actually been "
                "recorded, not backfilled from external history before that point."
            ),
        ),
    ]
}


def get_definition(metric_id: str) -> MetricDefinition | None:
    return _DEFINITIONS.get(metric_id)


def all_definitions() -> list[MetricDefinition]:
    return list(_DEFINITIONS.values())


def all_pillars() -> list[str]:
    return sorted({definition.pillar for definition in _DEFINITIONS.values()})
