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
| Strategy decisions | Partial | Runtime snapshots remain for compatibility; every evaluated setup is also persisted with a correlation id, policy evidence, execution result, and order/trade/position references. `GET /strategies/{strategy_id}/decisions` provides filtered restart-safe history. Exchange fill confirmation is not complete. |
| Position intents | Partial | Current position intent snapshots exist and `/positions/logical` exposes persisted logical units with trade backfill, per-unit close signal conditions, legacy trade rules, and reconciliation state for the frontend. |
| Confirmed live close execution | Partial | Armed live triggers submit guarded reduce-only market orders and wait for fills. Canceled/rejected pending exits recover safely, stale orders receive one cancellation request, and current OKX min/lot precision is validated before submission. Private websocket latency remains incomplete. |
| Logical position units | Partial | SQLite persistence, per-unit close conditions, reconciliation, idempotent open/close fill allocation, and manual close API are implemented. Private websocket cancellation events and break-even operations remain incomplete. |
| Execution fill ingestion | Partial | `ExecutionFillService` polls fills and pending orders, allocates idempotently, recovers terminal orders, cancels stale orders once, and emits one alert when a filled order lacks fill details for three polls. Durable REST cursors and private websocket latency remain planned. |
| Signal strategy execution | Built | Enabled SQLite strategies compose persisted entry expressions, resolve `self` per target, evaluate candle context, consume one persisted false-to-true edge, pass BTC policy, submit a validated order, and copy default close conditions to the new logical unit. |
| Strategy Management page | Partial | A frontend route exists, but the target strategy-management workflow is not complete. |
| Position Management page | Partial | A frontend route exists, but per-unit management and K-line overlays are not complete. |
| API contract generation | Partial | Runtime, strategy, signal-expression, and logical-position endpoints use Pydantic response models and OpenAPI; `scripts/generate_openapi_types.py` exports `docs/openapi.json`; frontend schema types and dashboard API helpers are typed from generated contracts. |
| Authentication/authorization | Planned | Required before exposing service or trading controls beyond localhost. |
| Structured persistence | Partial | SQLite stores exist for trades, logical positions, allocations, strategies, signal expressions, position-manager audits, and strategy decision/execution history; most general runtime events remain in-memory. |
| SQLite schema management | Partial | `TradeStore` records version `1`, `AuditEventStore` records version `2`, and `StrategyStore` and `LogicalPositionStore` record version `3` through explicit migration paths. All default to `MAYBECH_DB_PATH`. Strategy version 2 adds persisted edge state; version 3 removes legacy momentum records. |
| Live order protection | Built | Strategy contract counts are persisted per instrument. Entries and reduce-only closes validate OKX state, `minSz`, `lotSz`, and `tickSz`; entries require and attach a side-consistent exchange stop, with take profit attached when configured. |

## Next Build Milestones

1. Persist OKX fill-history pagination/catch-up cursors and add private order
   websocket events while retaining REST catch-up.
2. Complete demo-account open, partial-fill, cancellation, close, and restart
   verification before arming a live account.
3. Add authentication before any remote trading controls are exposed.
