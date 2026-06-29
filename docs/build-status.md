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
| Account snapshot | Partial | Current API exposes account summary, positions, and orders snapshots. |
| BTC regime state | Partial | `BTCRegimeService` publishes regime state and `/market/btc-regime` exposes the latest snapshot. |
| Strategy decisions | Partial | Runtime snapshots remain for compatibility; every evaluated setup is also persisted with a correlation id, policy evidence, execution result, and order/trade/position references. `GET /strategies/{strategy_id}/decisions` provides filtered restart-safe history. |
| Position intents | Partial | Current position intent snapshots exist and `/positions/logical` exposes persisted logical units with trade backfill, per-unit close signal conditions, legacy trade rules, and reconciliation state for the frontend. |
| Confirmed live close execution | Partial | Armed live triggers submit guarded reduce-only market orders and wait for fills. Private order events recover cancellations quickly, REST catch-up remains authoritative, stale orders receive one cancellation request, and current OKX min/lot precision is validated before submission. |
| Logical position units | Partial | SQLite persistence, per-unit close conditions, account-wide net-position reconciliation, exact-gap external import, immutable entry/close/protective-algo ownership, confirmed stop amendments and break-even operations, out-of-order fill allocation, and manual close API are implemented. Generic rule edits cannot desynchronize an owned stop. Position groups remain incomplete. |
| Execution fill ingestion | Built | A required authenticated `orders/SWAP` WebSocket applies fills and terminal cancellations with low latency and reconnects with bounded backoff. `ExecutionFillService` also traverses three-month OKX fill history by bill ID with durable checkpoints as the correctness layer. Both paths share idempotent allocation. Live entries remain blocked until REST is caught up and the private stream is connected. |
| Signal strategy execution | Built | Enabled SQLite strategies compose persisted entry expressions, resolve `self` per target, evaluate candle context, consume one persisted false-to-true edge, pass BTC policy, submit a validated order, and copy default close conditions to the new logical unit. |
| Strategy Management page | Partial | A frontend route exists, but the target strategy-management workflow is not complete. |
| Position Management page | Partial | The frontend lists typed logical units, independent quantity/status, close conditions, source strategy, owned protection lifecycle, and confirmed close control. Rule editing and K-line overlays remain incomplete. |
| API contract generation | Partial | Runtime, strategy, signal-expression, and logical-position endpoints use Pydantic response models and OpenAPI; `scripts/generate_openapi_types.py` exports `docs/openapi.json`; frontend schema types and dashboard API helpers are typed from generated contracts. |
| Authentication/authorization | Planned | Required before exposing service or trading controls beyond localhost. |
| Structured persistence | Partial | SQLite stores exist for trades, logical positions, allocations, strategies, signal expressions, position-manager audits, and strategy decision/execution history; most general runtime events remain in-memory. |
| SQLite schema management | Partial | `TradeStore` and `ExecutionCursorStore` record version `1`, `AuditEventStore` records version `2`, `StrategyStore` records version `3`, and `LogicalPositionStore` records version `6` through explicit migration paths. All default to `MAYBECH_DB_PATH`. |
| Live order protection | Built | Strategy entries are slippage-capped FOK orders using SDK-supported `attachAlgoOrds`; success requires complete fill plus exact active child-algo proof. Imported/recovered units create independently sized stops. Every unit persists unique algo ownership; live preflight and entry approval recheck active protection; trigger fills allocate by algo identity; software/manual closes cancel protection first and re-arm it after failed or partial-close recovery. |
| Live startup preflight | Built | Importing configuration never arms orders. `--live` disarms first, validates credentials through OKX account config, derivatives account level, `net_mode`, enabled strategy contracts/stops, active logical-position instruments, and live SWAP precision, then arms or aborts startup. `/runtime/preflight` exposes the successful report. |
| Account risk envelope | Built | One versioned SQLite record owns maximum order notional, gross account exposure, and leverage. Live startup requires it enabled; every entry uses fresh OKX positions, logical-unit reconciliation, pending entries, contract metadata, and leverage to issue a single-use approval before intent persistence or submission. `GET/PUT /risk/limits` manages it. |
| Entry kill switch | Built | Entries default to disabled in SQLite and have a separate process-local arm from reduce-only closes. Every live startup persists entries disabled and audits the reset; enable is rejected outside an armed live process. Confirmed enable/kill APIs serialize against strategy submission; kill persists first, resolves accepted orders by exchange or client ID, requests cancellation only for `pending_open` units, and reports partial failures without re-enabling. |
| Runtime ownership | Built | Every default runtime locks its normalized SQLite path, preventing dry/live state races. Authenticated live preflight also derives and locks a non-secret account scope from OKX `uid` and demo/real mode. Conflict aborts startup, `/runtime/lease` exposes ownership, and OS process death releases locks without expiry races. |

## Next Build Milestones

1. Complete the execution-confirmed logical-unit reduce lifecycle in
   `toImprove.md` using typed interfaces and mocks. Credential rotation remains
   a separate external gate: the disclosed key must not be used for any
   authenticated check, order-capable action, or live runtime start.

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
- 2026-06-29: the operator canceled the demo path because a demo key is not
  available and requested staged real-account preparation. Local state was
  confirmed fail-closed: `MAYBECH_ARM_ORDERS=0`, no account risk record, entries
  disabled, zero strategies, and zero active logical positions. The disclosed
  production key must be revoked and replaced before authenticated work resumes.
