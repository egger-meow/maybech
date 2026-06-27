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
| Confirmed live close execution | Partial | Armed live triggers submit guarded reduce-only market orders and wait for fills. Canceled/rejected pending exits recover safely, and stale orders receive one cancellation request. Private websocket latency and exchange-specific size validation remain incomplete. |
| Logical position units | Partial | SQLite persistence, per-unit close conditions, reconciliation, idempotent open/close fill allocation, and manual close API are implemented. Private websocket cancellation events and break-even operations remain incomplete. |
| Execution fill ingestion | Partial | `ExecutionFillService` polls fills and pending order states every five seconds, allocates matching fills idempotently, recovers terminal orders, cancels stale active orders once, and exposes detailed status. Private websocket latency remains planned. |
| Signal expression engine | Partial | Signal expressions can be persisted as JSON records under strategies, validated through `/signals/validate`, evaluated against caller-provided, runtime snapshot, or candle-derived context through `/signals/evaluate`, and required before strategy enable. |
| Strategy Management page | Partial | A frontend route exists, but the target strategy-management workflow is not complete. |
| Position Management page | Partial | A frontend route exists, but per-unit management and K-line overlays are not complete. |
| API contract generation | Partial | Runtime, strategy, signal-expression, and logical-position endpoints use Pydantic response models and OpenAPI; `scripts/generate_openapi_types.py` exports `docs/openapi.json`; frontend schema types and dashboard API helpers are typed from generated contracts. |
| Authentication/authorization | Planned | Required before exposing service or trading controls beyond localhost. |
| Structured persistence | Partial | SQLite stores exist for trades, logical positions, allocations, strategies, signal expressions, position-manager audits, and strategy decision/execution history; most general runtime events remain in-memory. |
| SQLite schema management | Partial | `TradeStore` and `StrategyStore` record version `1`, `AuditEventStore` records version `2`, and `LogicalPositionStore` records version `3` through explicit migration paths. All default to `MAYBECH_DB_PATH`. |

## Next Build Milestones

1. Reconcile and alert when OKX reports an order `filled` but fill details remain
   unavailable across repeated REST polls.
2. Add authenticated private OKX order websocket events for low-latency fills
   and state changes while retaining REST catch-up.
3. Add explicit retention/compaction for audit queries; timestamp pagination is
   implemented but needs an opaque stable cursor before multi-writer use.
4. Add break-even and grouped-position mutation/query endpoints.
5. Build the Strategy Management and Position Management pages around those
   contracts.
6. Add authentication and authorization before any remote trading controls.
