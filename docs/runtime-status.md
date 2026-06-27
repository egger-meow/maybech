# Runtime Status

This file records the current runtime/API tracking surface for Maybech. Update it
when endpoints, service state keys, or safety behavior changes.

## Local Runtime

- API entry point: `uv run python -m src.runtime api`
- Headless service runner: `uv run python -m src.runtime services`
- API compatibility wrapper: `uv run python run_api.py`
- Headless compatibility wrapper: `uv run python run_services.py`
- Recommended Python: `3.13`
- Supported Python range: `>=3.11,<3.15`

Runtime CLI parsing and startup now live in `src/runtime/`. The root-level
runner names are compatibility wrappers and should stay thin.

## Service State Contract

`GET /services` returns a map keyed by service name. Each service status includes:

- `name`: service name
- `active`: boolean used by frontends for running/stopped state
- `interval`: tick interval in seconds
- `last_tick`: latest tick time, or `null`
- `last_duration`: latest tick duration, or `null`
- `errors`: consecutive error count

Frontends must use `active`; there is no `state` field.

## Key API Snapshots

- `GET /account/snapshot` returns `{ "summary": {}, "positions": [], "orders": [] }`
  when no account data is available.
- Account summary equity fields are strings from OKX-style payloads:
  `total_equity`, `available_equity`, and optional `unrealized_pnl`.
- `GET /market/btc-regime` returns the latest BTC regime. `direction`,
  `strength`, and `impulse` are categorical strings, not guaranteed numbers.
- `GET /strategy/decisions` and `GET /position/intents` return empty lists when
  no runtime snapshot is available.
- `GET /execution/fills/status` returns the latest authenticated OKX SWAP fill
  polling counts, or zeroed fields before the first successful poll.
- `GET /strategies/{strategy_id}/decisions` returns restart-safe strategy
  decisions from SQLite, newest first. Filters cover allowed/blocked state,
  execution status, limit, and a `before` timestamp.
- `GET /strategies` returns typed persisted strategy summaries, including
  runtime service status, signal parameters,
  target instruments, default rule parameters, and latest decisions.
- `GET /positions/logical` returns typed read-only logical position units from
  `LogicalPositionStore`, with compatibility backfill from `TradeStore` records.
  These include persisted close signal conditions, legacy trade rule groups,
  current position intent, matching OKX net-position snapshot data when present,
  conservative reconciliation state, and related audit events when event
  payloads reference the trade/position id.
- `GET/POST/PATCH/DELETE /positions/logical/{position_id}/close-conditions`
  manages validated per-unit close signal expressions without executing orders.
- `GET/POST /positions/logical/{position_id}/allocations` lists or ingests
  confirmed execution fills. Fill IDs are idempotency keys; conflicting reuse
  returns `409`, and over-reduction is rejected before persistence.
- `GET /events` returns recent in-memory runtime events.
- `GET /audit/events` returns newest-first SQLite-backed audit records and can
  filter by event type, source, strategy/correlation id, logical position id,
  trade id, or a `before` timestamp. The durable subset covers position-manager
  condition evaluations/close attempts and strategy decisions/execution results.
- `ws://127.0.0.1:8000/ws/events` streams runtime events through
  `RuntimeState.events`.

## Target API Direction

`docs/api-spec.md` tracks the intended durable API surface. The most important
gap is a first-class distinction between:

- OKX net positions from exchange/account snapshots
- Maybech logical position units used for independent post-entry management

Frontend code should not assume one OKX position row equals one managed
position.

The current `/positions/logical` endpoint has persistent logical-unit storage,
per-unit close signal conditions, and conservative reconciliation against OKX
net-position snapshots. It is not yet the final execution-confirmed allocation
lifecycle described in `docs/domain-model.md`.
`PositionManagerService` now evaluates persisted close signal conditions against
runtime price/change context and optional candle-derived context for rapid-move
and volume-ratio conditions. In dry-run mode it can close the simulated logical
unit and backing trade. In armed live mode, triggered conditions automatically
submit a reduce-only market close without waiting for operator confirmation.
The unit atomically moves to `closing` and only confirmed fills reduce quantity
or close its backing trade.
Those evaluations and close outcomes are persisted in `audit_events`, including
invalid expressions and candle-context failures.

`StrategyService` writes an action-decision record before an allowed execution
and updates that record with the result. Live actions fail closed when this
pre-execution audit write fails. Dry-run orders are marked `simulated`; non-empty
live responses are only `submitted` until exchange fill reconciliation exists.
Live submissions now create `pending_open` trade/logical records with zero
allocated quantity. A confirmed open fill atomically creates the allocation,
updates weighted entry price and quantities, opens the unit for management, and
updates the correlated strategy decision to `partially_filled` or `filled`.
`ExecutionFillService` polls up to 100 recent authenticated SWAP fills every
five seconds. It normalizes OKX fill payloads, matches indexed exchange order
IDs, and replays safely because allocation IDs use immutable OKX trade IDs.
Unmatched manual/external orders remain unallocated and visible in status.
The same poll checks every unit with an active exchange order id. Confirmed
`canceled`, `rejected`, or `mmp_canceled` entry orders recover to `failed` when
unfilled or `open` when partially filled; canceled close/reduce orders recover
to `open` while quantity remains. Active orders older than five minutes receive
one cancellation request and remain pending until OKX confirms a terminal state.
If OKX reports `filled` but no matching fill details arrive for three consecutive
polls, the unit emits one deduplicated durable
`position.filled_without_allocation` alert for operator investigation.

## Runtime Storage

All SQLite stores resolve `MAYBECH_DB_PATH`, which defaults to
`data/trades.db`. An explicit constructor path still overrides the environment
setting for tests and isolated tools. Keep all production stores on one path so
the API and daemon observe the same schema and records.

Mutable strategy configuration is stored in the `strategies` table, not in
`.env`. Enabled strategies require target instruments, `metadata.position_side`,
per-instrument `metadata.order_size_contracts`, an entry signal expression, and
at least one exchange-attachable absolute stop-loss condition. The daemon composes
entry-purpose expressions with `and`, resolves `symbol: "self"` per target,
and persists match state so a continuously true signal creates only one entry,
including across restarts. Every new logical unit receives its own copy of the
strategy's default close conditions.

## Safety Notes

- Automatic signal/rule exits do not ask for human confirmation. Live startup,
  `MAYBECH_ARM_ORDERS=1`, the reduce-only client guard, and durable pre-submit
  audit must all succeed before an order is sent.
- Live entries require an explicit contract count for their instrument in the
  strategy record's `metadata.order_size_contracts`. Before entry or reduce-only
  close submission, Maybech fetches OKX metadata and rejects halted instruments,
  sizes below `minSz`, and sizes outside `lotSz`; limit and trigger prices are
  normalized to `tickSz` with decimal arithmetic.
- Every strategy entry carries its side-consistent absolute stop loss as an OKX
  attached market stop. A compatible take profit is attached when configured;
  rapid-move and composite exits continue to be managed per logical unit.
- `POST /positions/logical/{position_id}/close` is the separate manual operator
  command and requires `{ "confirm": true }` to prevent accidental clicks.
- Rule deletion must be scoped by both `trade_id` and `group_id` so one trade
  cannot delete another trade's rule group.
- The confirmed-fill POST is a trusted local ingestion boundary, not an order
  placement endpoint. Keep it localhost-only until service authentication and
  authorization are implemented.
- REST fill polling is the correctness/catch-up layer. A future private OKX
  websocket should reduce latency and report cancellations, but reconnects must
  still catch up through REST.
