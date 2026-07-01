# Build Status

This file tracks product build status at a coarse level. Update it when a major
capability moves from planned to partial or complete.

## Status Legend

- Built: implemented and usable in the current repo.
- Partial: implemented enough to inspect or prototype, but not complete.
- Planned: product direction is documented but implementation is missing.
- Blocked: intentionally held until safety or architecture prerequisites exist.

## Current Status

| Area | Status | Notes |
| --- | --- | --- |
| API-backed daemon runtime | Partial | `uv run python -m src.runtime api` starts FastAPI with daemon services; `run_api.py` remains a compatibility wrapper. |
| Headless daemon runtime | Partial | `uv run python -m src.runtime services` runs services without UI; `run_services.py` remains a compatibility wrapper. |
| Runtime events | Partial | `/events` and `/ws/events` provide the live in-memory stream; position-manager evaluations and close attempts are durable through `AuditEventStore` and `GET /audit/events`. Broader event persistence and retention are not built. |
| Notification scope and cooldown | Built | The legacy support/resistance and standalone fluctuation alert daemon, configs, calculations, LINE formatters, and tests are removed. A small daemon service routes only strategy, position, runtime-safety, and exchange/execution lifecycle audits to configured LINE and Gmail transports. Both suppress equivalent normalized messages using configurable `NOTIFICATION_COOLDOWN_SECONDS`; noisy market and rule-evaluation events are excluded. |
| Account snapshot | Partial | Current API exposes account summary, positions, and orders snapshots. |
| BTC regime state | Partial | `BTCRegimeService` publishes regime state and `/market/btc-regime` exposes the latest snapshot. |
| Strategy decisions | Partial | Runtime snapshots remain for compatibility; every evaluated setup is also persisted with a correlation id, policy evidence, execution result, and order/trade/position references. `GET /strategies/{strategy_id}/decisions` provides filtered restart-safe history. |
| Position intents | Partial | Current position intent snapshots exist and `/positions/logical` exposes persisted logical units with trade backfill, per-unit close signal conditions, legacy trade rules, and reconciliation state for the frontend. |
| Confirmed live close execution | Built | Armed live triggers submit guarded reduce-only orders and wait for confirmed fills before changing logical quantity or closing a unit. Private events reduce latency, REST catch-up remains authoritative, and terminal/partial recovery restores exact remaining protection. The bounded lifecycle has passed on demo and production. |
| Logical position units | Built | SQLite persistence, per-unit close conditions, typed grouping, account-wide reconciliation, exact-gap import, immutable order/protection ownership, confirmed stop amendments, break-even, exact partial reduce, out-of-order allocation, and manual close are implemented and exposed in the browser. |
| Execution fill ingestion | Built | A required authenticated `orders/SWAP` WebSocket applies fills and terminal cancellations with low latency and reconnects with bounded backoff. `ExecutionFillService` also traverses three-month OKX fill history by bill ID with durable checkpoints as the correctness layer. Both paths share idempotent allocation. Live entries remain blocked until REST is caught up and the private stream is connected. |
| Signal strategy execution | Built | Enabled SQLite strategies compose persisted entry expressions, resolve `self` per target, evaluate candle context, consume one persisted false-to-true edge, pass BTC policy, submit a validated order, and copy default close conditions to the new logical unit. |
| Optional strategy execution delay | Built | Each strategy persists `execution_delay_seconds` (default 0). Positive delays require initial policy/risk checks, then create restart-safe SQLite pending records and audit events. At due time the service rebuilds signal context and reruns signal, policy, ingestion, and risk checks; invalidated or blocked actions cancel with evidence before submission. The UI exposes delay and pending due/correlation state. |
| Strategy Management page | Built | The responsive frontend creates, edits, inspects, enables, disables, and safely deletes persisted strategies; it edits primary/child signal expressions and default position rules with nested AND/OR groups, explicit saved state, readiness, and decision evidence. |
| Position Management page | Built | The responsive frontend separates logical units from OKX net snapshots, renders typed K-line overlays, edits per-unit composite rules, uses confirmed stop-amend/break-even paths, shows protection and allocation evidence, and confirms close/reduce commands. |
| Manual logical-position open | Partial | Position Management provides a cached searchable selector, fresh-price limit prefill, display/API quantities, notional and stop-PnL preview. Confirmed Dry-run opens persist `source=manual`, initial stop/take-profit, allocation, and audit atomically. Live manual open remains intentionally blocked. |
| Existing-position recovery | Built | Every account snapshot compares OKX net positions with exchange-backed logical units. Clear startup/external increases create separate `source=recovery` units with audit evidence and manual-review/protection-required state. Position Management provides a guarded adoption flow that requires a side-correct stop, balanced OKX quantity, and exchange proof of an independently owned reduce-only stop before clearing review. External reductions never guess a unit or use that shortcut; they mark all affected units for manual review once per changed reconciliation signature. Pending opens and Dry-run units cannot be double-counted as recovery exposure. |
| API contract generation | Built | Product mutations and reads use Pydantic/OpenAPI contracts; generated TypeScript types drive dashboard helpers and both management pages, with checked-in drift checks. |
| Authentication/authorization | Partial | Loopback remains default. Non-loopback startup requires explicit `--allow-remote` plus a configured bearer token; HTTP, WebSocket, CORS, and frontend client helpers support it. TLS, user identities, and fine-grained roles remain external/future concerns. |
| Structured persistence | Partial | SQLite stores exist for trades, logical positions, allocations, strategies, editable signal expressions, product-definition mutation audits, position-manager audits, and strategy decision/execution history; most general runtime events remain in-memory. |
| SQLite schema management | Partial | `TradeStore` and `ExecutionCursorStore` record version `1`, `AuditEventStore` records version `2`, `StrategyStore` records version `3`, and `LogicalPositionStore` records version `6` through explicit migration paths. All default to `MAYBECH_DB_PATH`. |
| Live order protection | Built | Strategy entries are slippage-capped FOK orders using SDK-supported `attachAlgoOrds`; success requires complete fill plus exact active child-algo proof. Imported/recovered units create independently sized stops. Every unit persists unique algo ownership; live preflight and entry approval recheck active protection; trigger fills allocate by algo identity; software/manual closes cancel protection first and re-arm it after failed or partial-close recovery. |
| Live startup preflight | Built | Importing configuration never arms orders. `--live` disarms first, validates credentials, derivatives account level, `net_mode`, fresh OKX exposure reconciliation, protected logical units, enabled strategy contracts/stops, and SWAP precision, then arms or aborts startup. `/runtime/preflight` exposes the successful report. |
| OKX instrument metadata | Built | A versioned SQLite cache plus typed list/refresh and bidirectional size-quote APIs persist tradable SWAP sizing data. The daemon refreshes stale data at startup and before account ticks on a 24-hour TTL; APIs and selectors expose stale state and block sizing/manual-open mutations until refreshed. Strategy/manual inputs and position reduce controls use operator-facing base quantity while displaying exact OKX contracts/notional. Absolute-price rules show side-aware USDT PnL. |
| Account risk envelope | Built | One versioned SQLite record owns maximum order notional, gross account exposure, and leverage. Live startup requires it enabled; every entry uses fresh OKX positions, logical-unit reconciliation, pending entries, contract metadata, and leverage to issue a single-use approval before intent persistence or submission. `GET/PUT /risk/limits` manages it. |
| Live-readiness UI diagnostics | Built | The Traditional Chinese runtime banner distinguishes missing, ready, and not-yet-verified checks. A missing risk envelope names all three required numeric fields and enabled state; instrument cache, per-strategy API size/slippage/protective stop, OKX account level, and `net_mode` are shown separately. |
| Entry kill switch | Built | Entries default to disabled in SQLite and have a separate process-local arm from reduce-only closes. Every live startup persists entries disabled and audits the reset; enable is rejected outside an armed live process. Confirmed enable/kill APIs serialize against strategy submission; kill persists first, resolves accepted orders by exchange or client ID, requests cancellation only for `pending_open` units, and reports partial failures without re-enabling. |
| Runtime ownership | Built | Every default runtime locks its normalized SQLite path, preventing dry/live state races. Authenticated live preflight also derives and locks a non-secret account scope from OKX `uid` and demo/real mode. Conflict aborts startup, `/runtime/lease` exposes ownership, and OS process death releases locks without expiry races. |
| Horizontal runtime boundary | Partial | One explicit `combined` role owns execution and mutations; read-only `replica` roles start no daemon, reject execution flags/mutations/live routes/WebSocket events, and use SQLite `mode=ro` plus `query_only`. Same-host read scaling is bounded and testable; multi-host replicas still require shared transactional storage and authenticated leader routing. |

## Next Build Milestones

The first usable strategy-to-logical-position product loop is complete. No
active real-money execution blocker is recorded in `toImprove.md`. Unattended
entries remain disabled by default and require explicit runtime arming, strategy
enablement, and entry-gate confirmation. Backtesting/research and broader event
retention remain non-blocking later work; they are not compatibility paths for
this completed loop.

## Execution Verification Evidence

- 2026-06-29: a process-local `OKX_FLAG=1` read-only probe attempted account
  configuration and open-position queries. OKX rejected authentication with
  code `50110` because the current machine was not in the API key IP whitelist.
  No order endpoint was called. Open, cancellation, automatic close,
  protective-stop trigger, and restart recovery therefore remain unverified on
  an actual demo account. Configured private integration checks now fail on
  authentication errors instead of reporting a skipped, false-green run.
- 2026-06-29: after the IP whitelist was updated, the same read-only demo probe
  reached OKX but failed with code `50101` because the configured API key does
  not belong to the demo environment. A read-only `OKX_FLAG=0` check confirmed
  that the key belongs to the real account, which reports account level `2` and
  incompatible `long_short_mode`. No order endpoint was called. Verification
  requires a demo API key and a demo account configured for `net_mode`.
- 2026-06-29: dedicated production and demo keys were created and the previously
  disclosed key was revoked. Demo verification is active again and must use the
  `DEMO_OKX_*` credentials with `OKX_FLAG=1`. The historical failed probes above
  are retained as evidence only; they are not current blockers.
- 2026-06-29: demo run `e736d19c673e` completed against `BTC-USDT-SWAP` in
  `net_mode`: read-only preflight, minimum pending-order cancellation, protected
  `0.02`-contract FOK open, confirmed stop amendment, exact `0.01` reduce,
  protection restoration at `0.01`, final reduce-only close, fresh-service
  restart catch-up, and cleanup all passed. Final exchange checks found zero
  nonzero positions, pending orders, or pending algos. SQLite retained the
  closed unit, confirmed allocations, protection/close/reduce audits, and
  verifier events in `data/demo-lifecycle-5.db` (runtime evidence, not source).
  OKX demo did not expose immediate fills through recent or first-page archive
  queries, so the verifier used authenticated terminal order state,
  `accFillSz`, and `avgPx` as deterministic `recovery` allocations.
- 2026-06-29: dedicated production credentials authenticated successfully.
  Read-only inspection found zero nonzero SWAP positions, pending orders, and
  pending algos, with `net_mode` active. The account remains Spot level
  `acctLv=1`; an isolated full preflight failed before arming with the required
  derivatives-account-level error. Minimum-exposure production execution is
  therefore externally blocked until the operator changes the OKX account to
  Futures (`acctLv=2`), Multi-currency margin (`3`), or Portfolio margin (`4`).
- 2026-06-29: after the production sub-account changed to Futures
  (`acctLv=2`, `net_mode`), production run `e9c538e5a962` completed against
  `BTC-USDT-SWAP`: minimum pending-order cancellation, protected
  `0.02`-contract FOK open, confirmed stop amendment, exact `0.01` reduce,
  protection restoration at `0.01`, final reduce-only close, restart replay,
  and cleanup all passed. Restart replay recognized one fill idempotently.
  Independent final checks found zero nonzero positions, pending orders, or
  pending algos; SQLite retained the closed unit (`remaining=0`) and all
  production verification/audit events in `data/production-lifecycle-3.db`.
  Available USDT moved from `10` to `9.98881889` during the bounded proof.
