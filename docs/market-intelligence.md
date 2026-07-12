# Market Intelligence Layer

Tracks the market-regime workspace initiative from `plan.md` (operator-provided
strategic plan, 2026-07-12). Sections 1-6 are the Phase 0 deliverable: a
repository-grounded feasibility report and initial metric catalog. Section 7
records what Phase 1 built. Update this doc as later phases land; do not let
it drift into a changelog (see `toImprove.md` priority rules for the same
discipline).

## 1. What already exists

The plan assumed a green field. It is not one. `GET /market/macro-overview`
already implements a working, provider-agnostic macro data endpoint:

- `src/market/macro_overview.py` — fetches Fear & Greed, MVRV Z-Score, and
  CoinGecko global market data, each behind an in-process TTL cache
  (`_cache` module dict) with stale-on-error fallback. No retry/backoff beyond
  the cache. No SQLite persistence — cache is memory-only, lost on restart, not
  shared across API workers.
- `src/api/app.py:1606-1657` (`get_market_macro_overview`) wires it to the
  endpoint and adds OKX BTC/ETH price + OI-weighted funding outside simulation
  mode.
- `frontend/app/analysis/overview/page.tsx` renders it as five hand-rolled
  stat-card sections (global market, Fear & Greed meter + history, MVRV
  Z-Score + history, BTC/ETH price, funding/OI table) with a custom inline SVG
  line chart — no charting library.
- `GET /market/overview` (`src/api/app.py:1523-1578`) is a **separate,
  unrelated** endpoint: an all-instrument OKX ticker table for the instrument
  browser. It is not macro intelligence and should stay separate.
- `BTCRegimeService` (`src/daemon/btc_regime_service.py`,
  `src/market/btc_regime.py`) is OKX-candle-only (EMA trend/impulse/volatility
  classification). It does not consume macro data and is already a first-class
  regime input for strategy/position policy per `docs/system-direction.md`.
  It is a plausible future contributor to the new regime map's "Price &
  Breadth" pillar, not something to duplicate.

**Removed as part of this audit**: `src/data/market.py` (`MarketData` class) —
dead code, never imported anywhere in `src/`. It independently re-fetched Fear
& Greed from alternative.me (no cache) and MVRV from a second, undocumented,
unofficial source (`charts.bitbo.io`, scraped with spoofed browser headers) —
exactly the kind of unlicensed scraping `plan.md` §16 forbids, and a second
inconsistent MVRV number that would have confused any future reader. Deleting
it resolves the plan's Phase 0 exit criterion on MVRV naming/source ambiguity.

## 2. Verified provider capability matrix

| Provider | Endpoint | Auth | Verified constraints |
| --- | --- | --- | --- |
| alternative.me | `GET /fng/` (`?limit=0` for full history) | None | No documented rate limit; attribution required next to any displayed value (currently **not** rendered in the frontend — gap to close). Already used, TTL 300s. |
| bitcoin-data.com (BGeometrics) | `GET /v1/mvrv-zscore` | None currently | Daily granularity, JSON array `{d, unixTs, mvrvZscore}`, observed gaps (one `NaN` row seen in a spot check). Provider's own marketing page separately advertises a paid API with header-key auth and full 2009+ history for other metrics — the specific free `mvrv-zscore` endpoint in use today is unauthenticated, but this should be re-checked before Phase 1 in case free access narrows. Already used, TTL 3600s. |
| CoinGecko | `GET /api/v3/global` | None (public tier) | No auth/rate-limit headers observed in a direct fetch; the public tier's documented per-minute limit is unofficial and can tighten without notice — needs explicit backoff added (currently has none beyond TTL cache). Already used, TTL 300s. |
| DefiLlama | `GET https://stablecoins.llama.fi/stablecoins` (snapshot), `GET /stablecoincharts/all` (history) | None | Confirmed free, unauthenticated, returns per-asset circulating supply/chain breakdown and historical total mcap series. Not yet integrated. |
| Coin Metrics Community API | `https://community-api.coinmetrics.io/v4` | None | Confirmed key-free, but rate-limited to 10 req/6s/IP with reduced throughput vs. paid tiers. Exact free-tier coverage for MVRV/realized-cap/active-supply metrics was **not** confirmed (catalog query attempts failed with `400`); must be resolved with a proper catalog call before it is relied on for the Holder Behavior pillar. |
| OKX | existing `OKXClient` | API key (already configured) | No new verification needed; used for price, funding, candles today. Open interest (`GET /api/v5/public/open-interest`) is **not currently fetched anywhere** in the repo — needed for the Derivatives pillar's OI metrics and price/OI regime classification. |

## 3. Initial metric catalog

Provider-independent IDs per `plan.md` §5.1. `status` follows the plan's
`raw | derived | proxy` plus an implementation state.

### Price, Breadth, and Market Structure
| metric_id | source_kind | primary_provider | state |
| --- | --- | --- | --- |
| `okx_btc_price_usd` / `okx_eth_price_usd` | raw | OKX | live (macro_overview `fetch_prices`) |
| `global_market_cap_usd` / `global_volume_24h_usd` | raw | CoinGecko | live |
| `btc_dominance_pct` / `eth_dominance_pct` | raw | CoinGecko | live |
| `eth_btc_ratio` | derived | OKX (from the two prices above) | planned — trivial derivation, no new provider |
| `market_breadth_advancing_pct` | derived | CoinGecko markets list or OKX ticker sweep | planned, Phase 2+ |

### Derivatives and Leverage Pressure
| metric_id | source_kind | primary_provider | state |
| --- | --- | --- | --- |
| `okx_btc_funding_rate` | raw | OKX | live |
| `okx_oi_weighted_funding` | derived | OKX | live (already computed in `fetch_funding_overview`) |
| `okx_btc_oi_usd` | raw | OKX | **not implemented** — needs new OI ingestion, see matrix above |
| `okx_funding_annualized` | derived | OKX | planned, Phase 3 |
| `okx_price_oi_regime` | derived | OKX | planned, Phase 3, blocked on OI ingestion |

### On-Chain Valuation and Cycle Context
| metric_id | source_kind | primary_provider | state |
| --- | --- | --- | --- |
| `btc_mvrv_z` | raw (provider-computed) | bitcoin-data.com | live, persisted (Phase 1/2) |
| `btc_mvrv` | raw (provider-computed) | bitcoin-data.com | live, persisted (added post-Phase 2 — same source, sibling `/v1/mvrv` endpoint, `>1.0`/`<1.0` reads as aggregate holder profit/loss) |
| `btc_market_cap_usd` | raw | bitcoin-data.com or CoinGecko coin endpoint | planned — pick one canonical source in Phase 1, do not derive from `global_market_cap_usd * btc_dominance_pct` (compounds two approximations) |
| `btc_realized_cap_usd` / `btc_realized_price_usd` | raw | bitcoin-data.com (likely has dedicated endpoints alongside `mvrv-zscore`, per its metric list) | planned, Phase 1 endpoint discovery |
| `btc_mvrv_percentile` | derived | Maybech (from `btc_mvrv_z` history) | planned, Phase 3 — needs persisted history first |

### Holder Behavior
| metric_id | source_kind | primary_provider | state |
| --- | --- | --- | --- |
| `btc_supply_active_180d_pct` (proxy) | proxy | Coin Metrics Community, if catalog confirms | planned, Phase 1 catalog check required before commit |
| `btc_dormant_supply_180d_proxy_pct` | proxy | Coin Metrics Community, if catalog confirms | planned, same blocker |

No holder-behavior metric ships without a confirmed, documented Coin Metrics
Community catalog entry. If the community tier does not expose it, this pillar
stays `unavailable` rather than getting a scraped or invented proxy.

### Liquidity and Capital Availability
| metric_id | source_kind | primary_provider | state |
| --- | --- | --- | --- |
| `stablecoin_total_mcap_usd` | raw | DefiLlama | planned, Phase 2 |
| `stablecoin_mcap_change_7d_pct` / `_30d` | derived | DefiLlama (`stablecoincharts/all`) | planned, Phase 3 |

### Sentiment and Extremes
| metric_id | source_kind | primary_provider | state |
| --- | --- | --- | --- |
| `crypto_fear_greed` | raw | alternative.me | live, no persisted history, **attribution missing in UI** |
| `crypto_fear_greed_avg_7d` / `_30d` | derived | Maybech | planned, Phase 3 |
| `crypto_fear_greed_percentile` | derived | Maybech | planned, Phase 3 |
| `days_since_fear_greed_extreme` | derived | Maybech | planned, Phase 3 |

## 4. Naming ambiguity resolution

- **MVRV**: exactly one live source (bitcoin-data.com), after removing the
  bitbo.io duplicate. Both `btc_mvrv_z` (Z-score) and `btc_mvrv` (raw ratio)
  are provider-computed (BGeometrics methodology, sibling `/v1/mvrv-zscore`
  and `/v1/mvrv` endpoints), not a "MVRV Score." A Maybech-derived
  `btc_mvrv_percentile` is allowed later only as an explicitly named,
  separately versioned derived metric.
- **LTH/STH**: no implementation exists anywhere in the repo today. Any future
  holder-behavior metric must ship labeled `proxy` (e.g.
  `btc_supply_active_180d_pct`) until a provider with documented cohort
  methodology is integrated — matching `plan.md` §4.4/§9.
- **OI-weighted funding**: already implemented and OKX-scoped
  (`okx_oi_weighted_funding`); the ID keeps the `okx_` prefix per the plan's
  rule that venue-scoped derivatives data must not be labeled market-wide.

## 5. Architecture decision

Extend, don't replace. `src/market/macro_overview.py`'s three provider
functions are the seed for typed provider adapters, not a parallel
implementation to build around.

```text
src/market_intelligence/
  models.py          # MetricDefinition, MetricObservation, DerivedEvidence, RegimeAssessment
  registry.py         # static code registry (Section 5.1 shape), seeded from the catalog above
  service.py           # orchestrates providers -> storage -> derived -> regime
  freshness.py
  providers/
    base.py            # timeout/retry/backoff contract (missing from macro_overview.py today)
    okx.py              # wraps existing OKXClient calls (price, funding, OI once added)
    alternative_me.py   # migrated from macro_overview.fetch_fear_greed
    bitcoin_data.py      # migrated from macro_overview.fetch_mvrv_zscore
    coingecko.py          # migrated from macro_overview.fetch_global_market
    defillama.py           # new, Phase 2
  storage/
    metric_store.py         # market_metric_observations, market_provider_sync_runs
    migrations.py            # via existing src/trading/sqlite_schema.py helpers
  derived/                    # Phase 3
  regime/                      # Phase 4
```

- The four proposed table names (`market_metric_observations`,
  `market_provider_sync_runs`, `market_derived_calculations`,
  `market_regime_assessments`) are free — no collision with any existing
  `CREATE TABLE` in `src/trading/*.py`.
- `GET /market/macro-overview` keeps its current URL and response contract
  through Phase 1/2 so the existing frontend page does not break; it becomes
  backed by `market_intelligence.service` instead of calling the three
  provider functions directly. New typed endpoints (`/market/metrics`,
  `/market/series/{id}`, `/market/regime`, `/market/providers/status`) are
  additive, per `plan.md` §10.
- `GET /market/overview` (OKX ticker table) and `BTCRegimeService` are left
  alone; they solve different problems and already work.
- This keeps `docs/system-direction.md`'s refactor priorities and the existing
  `sqlite_schema.py` migration ledger as the storage authority — no new ORM,
  matching `docs/storage.md`.

## 6. Open items before Phase 1 can close its own exit criteria

1. Confirm Coin Metrics Community catalog coverage for MVRV/realized-cap/
   active-supply metrics with a working catalog query (the attempted fetch
   returned `400`; needs a corrected query, not a capability gap necessarily).
2. Confirm whether bitcoin-data.com's free `mvrv-zscore` endpoint has an
   undocumented per-IP rate limit before raising ingestion frequency beyond the
   current 3600s TTL.
3. Decide the canonical BTC market-cap/realized-cap source (bitcoin-data.com
   vs. CoinGecko coin endpoint) — do not derive it from two approximations.
4. Add OKX open-interest ingestion (no existing code fetches it).
5. Add Fear & Greed attribution text to the frontend per alternative.me's
   usage terms — currently missing.

None of these block starting Phase 1's domain-model/registry/storage spine;
items 1-4 block only the specific metrics they name, and Phase 1's freshness
model already requires marking unresolved metrics `unavailable` rather than
guessing.

## 7. Phase 1: Market Intelligence Foundation — delivered

Built `src/market_intelligence/`: `models.py` (`MetricDefinition`,
`MetricObservation`, `ProviderSyncRun`), `registry.py` (static catalog for the
six metrics with a live provider — see below), `freshness.py`
(`fresh`/`stale`/`very_stale`/`unavailable`, stale beyond 1x TTL, very_stale
beyond 3x TTL), `providers/base.py` (retry/backoff contract, classified
`ProviderError`), three provider adapters migrated from
`src/market/macro_overview.py` (`providers/alternative_me.py`,
`providers/bitcoin_data.py`, `providers/coingecko.py`), `storage/metric_store.py`
(SQLite, schema component `market_intelligence` v1: `market_metric_observations`
with a `(metric_id, source_provider, observed_at)` uniqueness constraint so a
conflicting re-insert is ignored rather than overwriting prior data, and
`market_provider_sync_runs` for provider-health history), and `service.py`
(`MarketIntelligenceService`: per-provider due-check against its own
`min_refresh_interval_seconds`, isolates one provider's failure from every
other provider and from API responses, computes freshness on read).

`MarketIntelligenceSyncService` (`src/daemon/market_intelligence_service.py`)
registers in `create_default_runner` alongside `BTCRegimeService` — it has no
exchange dependency, so it runs in every mode including simulation, and is
deliberately **not** in `required_services`: a setup/tick failure degrades its
own metrics, never blocks runtime startup.

Four new read-only endpoints (`GET /market/metrics`,
`GET /market/metrics/{metric_id}`, `GET /market/series/{metric_id}`,
`GET /market/providers/status`) are typed, exported through OpenAPI, and
consumed via the generated frontend types (`npm run contract` passes). They do
not replace `GET /market/macro-overview`, which keeps its existing contract
per the Phase 0 architecture decision.

Verified end-to-end against the real providers (not just fixtures): a
simulation-mode server run ingested live Fear & Greed (30-day history),
BTC MVRV Z-Score, and CoinGecko global/dominance data, served it through all
four endpoints with correct freshness, and a full process restart against the
same SQLite path preserved the ingested history — the exact Phase 1 exit
criteria in `plan.md` §13.

Deliberately out of scope for Phase 1 (deferred to Phase 2+): OKX open
interest ingestion, DefiLlama/Coin Metrics providers, derived metrics
(annualized funding, percentiles, rolling averages), regime assessment, and
any Market Overview UI change. `GET /market/macro-overview`'s in-memory-only
cache is untouched — a future phase may rewire it onto
`MarketIntelligenceService` once the UI is ready to consume typed metrics
directly, per the Phase 0 architecture decision in Section 5.

## 8. Phase 2: Useful Overview MVP — backend delivered, UI next

Added two more providers, reusing existing tested code where it already
existed:

- `providers/okx.py` (`OKXMarketProvider`): wraps the *existing*
  `src/market/macro_overview.py` `fetch_prices`/`fetch_funding_overview`
  functions (open interest was already being fetched there via
  `client.get_open_interest` — the Phase 0 audit's claim that OI ingestion
  was missing was wrong) into 7 metrics: `okx_btc_price_usd`,
  `okx_eth_price_usd`, `okx_btc_funding_rate`, `okx_eth_funding_rate`,
  `okx_btc_oi_usd`, `okx_eth_oi_usd`, `okx_oi_weighted_funding` (the last one
  `source_kind=derived`, reusing the already-tested weighting math). All are
  OKX-only (`scope` prefixed `okx_`/`okx`), unit `usd` for OI (settlement
  currency ≈ USDT for these instruments).
- `providers/defillama.py` (`DefiLlamaStablecoinProvider`): sums
  `circulating.peggedUSD` across every entry in `GET
  https://stablecoins.llama.fi/stablecoins`'s `peggedAssets` array into
  `stablecoin_total_mcap_usd`. Confirmed free/unauthenticated in Phase 0;
  confirmed correct field names (`peggedAssets`, `circulating.peggedUSD`) by
  fetching the live schema before writing the parser rather than guessing.

`OKXMarketProvider` needs a live exchange client and has no feed in
simulation mode (same as `GET /market/overview` and the price/funding section
of `GET /market/macro-overview`). Rather than skip registering it in
simulation, it is always registered with `client=None` there and reports
`is_configured() == False`; `MarketIntelligenceService.sync_provider` checks
`is_configured()` before the due-check and records a `skipped` run with
`error_category="not_configured"` instead of a permanent `failed` run every
tick. `create_default_runner` passes a real `OKXClient()` whenever the
resolved mode is not simulation.

**Bug caught and fixed during live verification, not just unit tests**: the
first implementation had `GET /market/providers/status`'s `enabled` field
call `provider.is_configured()` on the *instance serving that one API
request* — which is always a fresh `MarketIntelligenceService()` with no
exchange client, so it always reported `okx_market` as disabled regardless of
whether the long-running daemon's instance was actually configured and
successfully syncing. A live demo-mode run against real OKX credentials
surfaced this (`enabled: false` while `last_success_at` was populated with a
real timestamp). Fixed by deriving `enabled` from the most recent *persisted*
sync run's `error_category` instead of any single instance's live state,
since the SQLite history is the one thing every process shares. Verified
fixed against the same live server afterward.

Live-verified end to end in demo mode against real OKX credentials and real
DefiLlama data: real BTC/ETH prices, funding rates, open interest, and a
$311B total stablecoin figure were ingested and served correctly through
`GET /market/metrics` and `GET /market/providers/status` in the same run.

Registry now has 15 metrics across `sentiment`, `valuation`, `price_breadth`,
`derivatives`, and `liquidity` pillars — `holder_behavior` remains
unregistered pending the Coin Metrics Community catalog confirmation from
Phase 0 §6. (`btc_mvrv`, the raw MVRV ratio alongside `btc_mvrv_z`, was added
after this section was first written — see §10.)

Still open for Phase 2 to be "done" per `plan.md` §13: the Market Overview UI
redesign around regime pillars, historical charts with timeframe controls,
per-metric freshness/source/caveat display, and removing the current page's
misleading global "auto-refreshes" wording. `GET /market/macro-overview` and
its frontend page (`frontend/app/analysis/overview/page.tsx`) are still
untouched as of this section.

## 9. Phase 2: Market Overview UI redesign — delivered

`frontend/app/analysis/overview/page.tsx` is rewritten to consume the typed
`/market/metrics`, `/market/series/{metric_id}`, and `/market/providers/status`
endpoints instead of `/market/macro-overview`. `GET /market/macro-overview`
and its response contract are left untouched (still used nowhere else) per
the Phase 0 decision to keep it stable rather than force a rewire before the
UI actually needed it.

Layout: six pillar cards (`price_breadth`, `derivatives`, `valuation`,
`liquidity`, `sentiment`, `holder_behavior`), each showing every registered
metric's value and a per-metric freshness badge (`fresh`/`stale`/
`very_stale`/`unavailable`), plus one history chart for the pillar's
headline metric with its source/scope/caveat text underneath. No regime
`state`/`confidence` badge is shown anywhere — that would require the
regime-assessment engine (Phase 4), and inventing one now would violate
`plan.md` §9 ("the regime layer may not silently invent certainty"). The
`holder_behavior` pillar has no registered metrics yet and says so plainly
("尚無可用、有文件依據的免費資料來源") rather than being hidden or faked.

Page header shows a provider-health strip (`N/M 資料來源正常`, derived from
`/market/providers/status`), the freshest observed timestamp across all
metrics, and a per-source cadence disclosure line replacing the old page's
single "每 60 秒更新" claim, which was untrue for every metric except the
live OKX ticker feed.

A global timeframe control (7d/30d/90d/all) computes a `start` bound
client-side and re-fetches every pillar's chart series. A pillar's chart
metric with fewer than 2 accumulated points shows "尚未累積足夠歷史資料以繪製圖表"
instead of a fabricated or flat line — expected for metrics that only
accumulate forward from first ingestion (everything except
`crypto_fear_greed` and `btc_mvrv_z`, which the providers backfill on first
sync).

The existing hand-rolled inline-SVG `LineChart` (no charting library
dependency, matching the prior page's approach) gained a hover crosshair,
point marker, and floating tooltip (value + full timestamp) — the prior
version had no interaction at all. Followed the repo's dataviz skill: one
hue per chart (the existing `--accent-primary` design token, not a new
palette), thin 2.5px line, recessive axis labels, single-series charts carry
no legend (title names the series).

Two lint-driven fixes worth noting for future chart work in this repo: (1)
`react-hooks/purity` rejects `Date.now()` anywhere in the render body,
including inside `useMemo` — the timeframe boundary must be computed only in
a `useState` lazy initializer or an event handler, never derived during
render; (2) a `useMemo` dependency array must not include a value created
fresh every render via `?? []` fallback, or the memo never actually memoizes.

Live-verified in the browser (not just `npm run build`) against the real
demo-mode backend: all six pillars rendered real OKX/CoinGecko/DefiLlama/
alternative.me/bitcoin-data.com data, the provider-health strip correctly
showed 5/5, the timeframe control correctly changed the MVRV chart's date
range when clicked, the hover tooltip correctly displayed value and
timestamp, and dark mode rendered with correct contrast on every badge and
chart. Screenshots taken during verification are not checked in; the
behavior above is the durable record.

`npm run contract`, `npm run lint`, `npm run typecheck`, and `npm run build`
all pass. This closes `plan.md` §13's Phase 2 exit criteria: the page stays
useful when any one provider is offline (each metric/pillar degrades
independently), no metric is shown without scope/source/freshness, and
desktop/dark-mode layouts remain inspectable.

## 10. Post-Phase-2 addendum: raw MVRV ratio

Operator request: show MVRV as more than just the Z-score. bitcoin-data.com
has a sibling endpoint, `GET https://bitcoin-data.com/v1/mvrv`, same shape as
`/v1/mvrv-zscore` (`{d, unixTs, mvrv}`), free, unauthenticated — verified
before implementing, not assumed.

`BitcoinDataMvrvProvider` (`src/market_intelligence/providers/bitcoin_data.py`)
now fetches both endpoints per sync and parses each independently: one
failing (network error or unexpected shape) no longer discards observations
already obtained from the other, which the single-endpoint Phase 2 version
could not do. New registry entry `btc_mvrv` (unit `ratio`, pillar
`valuation`, methodology `bgeometrics_mvrv_ratio_v1`) documents the reading
convention (`>1.0` aggregate profit, `<1.0` aggregate loss) directly in its
`caveats` field. `btc_mvrv_z` stays the pillar's chart target; `btc_mvrv` is
a second tile in the same pillar card, no separate chart.

Live-verified end to end after finding and fixing a testing-only mixup (not
a code bug): `--mode demo` reads `DEMO_MAYBECH_DB_PATH`, not
`MAYBECH_DB_PATH` — an env var override aimed at the wrong variable caused a
verification run to silently reuse an old persisted demo database, where the
provider's hourly `min_refresh_interval_seconds` due-check correctly skipped
re-fetching a metric it had "already" synced 25 minutes earlier. Isolating
the provider in a standalone script proved the parser itself was correct
before chasing the real cause. With the correct env var, a fresh demo run
showed `btc_mvrv_z=0.3854` and `btc_mvrv=1.214` for the same day, both
`fresh`, both live.
