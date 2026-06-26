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
| Runtime events | Partial | In-memory event stream exists through `/events` and `/ws/events`; durable event storage is not built. |
| Account snapshot | Partial | Current API exposes account summary, positions, and orders snapshots. |
| BTC regime state | Partial | `BTCRegimeService` publishes regime state and `/market/btc-regime` exposes the latest snapshot. |
| Strategy decisions | Partial | Current decision snapshots and persisted strategy definition endpoints exist; decision history is still runtime-only. |
| Position intents | Partial | Current position intent snapshots exist and `/positions/logical` exposes persisted logical units with trade backfill and reconciliation state for the frontend. |
| Confirmed live close execution | Blocked | Live rule exits must not mark trades closed until OKX close/reduce orders are placed and confirmed. |
| Logical position units | Partial | SQLite persistence exists in `LogicalPositionStore`, with read-only API exposure, trade backfill, and conservative OKX net-position reconciliation; execution-confirmed allocation and live close execution are not complete. |
| Signal expression engine | Partial | Signal expressions can be persisted as JSON records under strategies, but validation/evaluation is not built. |
| Strategy Management page | Partial | A frontend route exists, but the target strategy-management workflow is not complete. |
| Position Management page | Partial | A frontend route exists, but per-unit management and K-line overlays are not complete. |
| API contract generation | Partial | Runtime, strategy, signal-expression, and logical-position endpoints use Pydantic response models and OpenAPI; `scripts/generate_openapi_types.py` exports `docs/openapi.json`; frontend schema types and dashboard API helpers are typed from generated contracts. |
| Authentication/authorization | Planned | Required before exposing service or trading controls beyond localhost. |
| Structured persistence | Partial | SQLite stores exist for trades, logical positions, allocations, strategies, and signal expressions; runtime events and decision history are still in-memory. |
| SQLite schema management | Partial | `TradeStore`, `LogicalPositionStore`, and `StrategyStore` record schema version `1` through the shared SQLite schema helper; future version upgrades still need explicit migration steps. |

## Next Build Milestones

1. Add signal-expression validation/evaluation and require validation before
   enabling a strategy for runtime execution.
2. Define persisted audit event and decision-history schemas using the shared
   SQLite schema helper and explicit migration steps.
3. Add execution-confirmed allocation of partial fills, fees, and close/reduce
   quantities to logical position units.
4. Add mutation endpoints for logical position
   management.
5. Build the Strategy Management and Position Management pages around those
   contracts.
6. Implement confirmed live close/reduce execution before enabling live
   position-manager exits.
