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
  Each successful account snapshot conservatively reconciles OKX net exposure:
  a clear unexplained size increase creates a separate `source=recovery`
  logical unit; an external decrease only marks affected units for manual
  review and never guesses allocation. Pending Maybech opens suppress automatic
  recovery for the same instrument/side.
- Account summary equity fields are strings from OKX-style payloads:
  `total_equity`, `available_equity`, and optional `unrealized_pnl`.
- `GET /market/btc-regime` returns the latest BTC regime. `direction`,
  `strength`, and `impulse` are categorical strings, not guaranteed numbers.
- `GET /market/candles` returns typed ascending OHLCV candles with exchange
  confirmation state for one instrument and interval.
- `GET /strategy/decisions` and `GET /position/intents` return empty lists when
  no runtime snapshot is available.
- `GET /execution/fills/status` returns the latest authenticated OKX SWAP fill
  catch-up counts, client-order recovery counts, cursor state, page progress,
  or zeroed fields before the first poll.
- `GET /runtime/preflight` returns the successful startup safety report,
  including dry-run/demo/real mode, armed state, OKX account and position mode,
  hashed account scope, enabled strategy count, account-risk enabled state,
  validated instruments, and check time.
- `GET /runtime/lease` reports exclusive live ownership of the resolved SQLite
  path and, in live mode, the hashed OKX account scope. Dry-run also holds the
  database lock so it cannot consume signal state beside another runtime.
- `GET /runtime/capabilities` reports whether the process is the combined
  execution leader or a read-only API replica and exposes routing/storage
  constraints plus authentication state without claiming distributed SQLite
  write support.
- `GET/PUT /risk/limits` reads or replaces the singleton SQLite account risk
  envelope used for live order-notional, gross-exposure, and leverage checks.
- `GET /risk/entries` reports persisted and process-local entry state.
- `GET /instruments` reads the SQLite-cached live OKX SWAP catalog. It returns
  `503` when metadata has not been refreshed; no synthetic instruments or unit
  defaults are returned. `POST /instruments/refresh` replaces that cache from
  the OKX public API.
- `POST /instruments/{inst_id}/size-quote` maps a base-asset display quantity to
  an exact OKX API contract count and estimated USDT notional. Ambiguous
  contract currency and invalid lot/minimum alignment fail closed.
  Confirmed `POST /risk/entries/enable` and `POST /risk/entries/kill` commands
  change that state; kill also requests cancellation of Maybech pending entries.
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
- `POST /positions/manual-open` creates a simulated `source=manual` unit in
  Dry-run after cached-instrument sizing and stop-direction validation. It
  records a confirmed simulated allocation and durable audit event. Demo/real
  calls are rejected before persistence in this build.
- `GET /positions/logical/{position_id}/chart` returns recent candles plus
  entry, current, enabled rule, break-even, and confirmed execution overlays.
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
Imported/recovered units report `protection_required` and block every new live
entry until a quantity-scoped, reduce-only OKX conditional stop is visible in
the pending-algo API with the expected side, size, and trigger. Entry approval
rechecks that live OKX order instead of trusting the persisted verification bit.
Fresh account-wide reconciliation also includes exchange-only net exposure and
infers long/short from signed OKX `pos` values in `net_mode`. Every final live
entry approval fails closed unless all active OKX SWAP quantities match active
logical-unit quantities. `GET /account/exposure-reconciliation` exposes that
fresh report. `POST /positions/import` can adopt only the measured unexplained
gap as one atomic logical unit with required close conditions; it cannot accept
a caller-selected quantity. Import also places and verifies the exchange stop.
`POST /positions/logical/{position_id}/protection` retries or safely amends the
same deterministic algo order after recovery or close-condition edits.

The current `/positions/logical` endpoint has persistent logical-unit storage,
per-unit close signal conditions, conservative reconciliation against OKX
net-position snapshots, and the execution-confirmed allocation lifecycle
described in `docs/domain-model.md`.
`PositionManagerService` now evaluates persisted close signal conditions against
runtime price/change context and optional candle-derived context for rapid-move
and volume-ratio conditions. In dry-run mode it can close the simulated logical
unit and backing trade. In armed live mode, triggered conditions automatically
submit a reduce-only market close without waiting for operator confirmation.
The unit atomically moves to `closing` and only confirmed fills reduce quantity
or close its backing trade.
`POST /positions/logical/{position_id}/reduce` performs the same confirmed
lifecycle for an exact partial quantity. The unit remains `reducing` across
partial fills, reuses one client order id after response loss or restart, and
returns to `open` only after target completion or terminal recovery. Its owned
stop is restored at the exact confirmed remainder before normal management.
Those evaluations and close outcomes are persisted in `audit_events`, including
invalid expressions and candle-context failures.

The product-definition API provides complete persisted strategy and child
signal-expression CRUD. Deleting a strategy requires it to be disabled and
unreferenced by logical-position history. Strategy, signal-expression, and
logical-position close-condition mutations produce durable `product_api` audit
events with before/after evidence in the same SQLite transaction as the
mutation. Audit failure rolls back the definition change. Strategy deletion
checks both logical-position and legacy trade history. `GET /positions/groups`
provides complete typed summaries for the final position-management frontend;
strategy groups remain financially valid by partitioning instrument and side.

`StrategyService` writes an action-decision record before an allowed execution
and updates that record with the result. Live actions fail closed when this
pre-execution audit write fails. Dry-run orders are marked `simulated`; live
orders are `submitted` only after a slippage-capped FOK order is completely
filled and its exact attached stop is visible in OKX's active algo list. SQLite
fill allocation still arrives asynchronously through authenticated ingestion.
Live submissions now create `pending_open` trade/logical records with zero
allocated quantity and persist a unique OKX `clOrdId` before network submission.
The eventual `ordId` is linked from the submission response, authenticated
order lookup, or fill after a restart. A confirmed open fill atomically creates the allocation,
updates weighted entry price and quantities, opens the unit for management, and
updates the correlated strategy decision to `filled`. A strategy FOK entry is
not allowed to remain partially filled; that anomalous state cancels the
remainder and disables future entries.
If a completely filled FOK entry cannot prove its active attached stop, the
runtime disables future entries and persists a deterministic reduce-only
emergency-close intent before submitting it. Missing submissions remain
retryable across restarts. Parent-entry and emergency-close order IDs retain
immutable ownership of the same logical unit, and an early close fill is stored
without changing quantity until the opening fill arrives and both allocations
can be applied in order.
Each active unit's attached or standalone stop is persisted as a unique
protection owner. Live startup and every subsequent entry approval prove its
exact active OKX algo, protected quantity, and stop level. Triggered normal
orders are linked by `algoId`/`algoClOrdId` before their fills are allocated.
Software and operator closes cancel and prove removal of the owned algo before
submitting a reduce-only market order. Unknown close acceptance keeps the same
client order id for bounded retries and restart recovery. A canceled or partial
close re-arms protection at the remaining quantity; failed re-arm kills future
entries.
Confirmed stop edits use the same owned algo and execution lock. The backend
persists an amend intent, verifies the old stop, submits the amendment, verifies
the new pending stop, and only then publishes the new close-condition value.
A proved old stop leaves the old rule unchanged; an ambiguous amend marks the
protection failed and disables entries. Generic close-condition mutations cannot
alter or delete an owned active stop.
Preflight, entry approval, and amendments also require owned protection quantity
to equal the unit's remaining logical quantity; an exchange order matching a
stale persisted size is rejected.
`POST /positions/logical/{position_id}/break-even` applies entry-price or
protected-profit stops through that same confirmed amendment path. It requires
explicit confirmation and a favorable current ticker beyond the
directionally-rounded target, then persists break-even evidence on the stop
condition and audit record.
`ExecutionFillService` polls authenticated three-month SWAP fill history every
five seconds. It also consumes authenticated private `orders/SWAP` events every
daemon cycle for low-latency fills and terminal cancellations. Login and
subscription acknowledgement are required during live startup; reconnects use
bounded backoff. REST traverses at most five 100-record pages per tick using OKX
`billId` pagination. SQLite stores a committed high-water bill ID separately
from an in-progress target and next-page checkpoint. A page checkpoint advances
only after every record is allocated, recognized as unmatched, or durably
quarantined; the high-water mark advances only when the prior boundary is found
or history is exhausted. Interrupted pages replay safely because allocation IDs
are idempotent. It normalizes OKX fill payloads and matches indexed exchange or
client order IDs. Persisted client-order intents with no `ordId` are queried by
`clOrdId`; accepted orders are linked, while stale intents absent from OKX fail
an entry or release a normal close back to `open`. Emergency-close intents stay
in `closing` state for deterministic retry instead of being released.
Unmatched manual/external orders remain unallocated and visible in status.
The same poll checks every unit with an active exchange order id. Confirmed
`canceled`, `rejected`, or `mmp_canceled` entry orders recover to `failed` when
unfilled or `open` when partially filled; canceled close/reduce orders recover
to `open` while confirmed quantity remains, and canceled protection is re-armed
at that exact remainder. Active orders older than five minutes receive
one cancellation request and remain pending until OKX confirms a terminal state.
If OKX reports `filled` but no matching fill details arrive for three consecutive
polls, the unit emits one deduplicated durable
`position.filled_without_allocation` alert for operator investigation.
`GET /execution/fills/status` exposes `caught_up`, page counts, cursor progress,
high-water/next-after bill IDs, history exhaustion, and cursor errors.
It also exposes `client_orders_linked` and
`missing_client_orders_recovered` for crash-window recovery visibility, plus
`protection_triggers_linked`, `protections_checked`, `protection_rearmed`, and
`protection_errors`.

`OKXClient.get_fills` also exposes the current authenticated fills endpoint for
bounded low-latency verification. The demo lifecycle verifier falls back to a
deterministic `recovery` allocation only when both fill endpoints lag and an
authenticated order query proves terminal `filled` state, positive
`accFillSz`, and positive `avgPx`. Normal runtime allocation still uses private
order events and durable REST history catch-up.
It also exposes private-stream connectivity, event/reconnect/drop counts, and
latest stream message/error details. WebSocket and REST share the same idempotent allocation
boundary. Live entries require current REST catch-up and a connected stream;
automatic position closes remain independent.

## Runtime Storage

All SQLite stores resolve `MAYBECH_DB_PATH`, which defaults to
`data/trades.db`. An explicit constructor path still overrides the environment
setting for tests and isolated tools. Keep all production stores on one path so
the API and daemon observe the same schema and records.

Mutable strategy configuration is stored in the `strategies` table, not in
`.env`. Enabled strategies require target instruments, `metadata.position_side`,
per-instrument `metadata.order_size_contracts`, a positive
`metadata.max_entry_slippage_pct` no greater than `0.05`, an entry signal
expression, and at least one exchange-attachable absolute stop-loss condition.
The daemon composes
entry-purpose expressions with `and`, resolves `symbol: "self"` per target,
and persists match state so a continuously true signal creates only one entry,
including across restarts. Every new logical unit receives its own copy of the
strategy's default close conditions.

## Safety Notes

- Importing `src.exchange.client` never arms orders. Every runtime factory
  disarms first. With `--live`, startup requires non-empty private credentials,
  `MAYBECH_ARM_ORDERS=1`, `OKX_FLAG` of `0` or `1`, authenticated account config,
  account level `2`, `3`, or `4`, and `net_mode`. Enabled strategy definitions,
  contract sizes, attached stops, active logical-position instruments, live
  SWAP metadata, and an enabled SQLite account risk envelope must validate
  before the factory arms order placement. Any
  failure aborts startup before service setup or daemon threads.
- Immediately before a live entry, Maybech calculates requested USD notional
  from current linear USDT-SWAP contract metadata. It adds gross `notionalUsd`
  across open SWAP positions and the remaining notional of all non-reduce-only
  pending SWAP orders, then checks the persisted order, total exposure, and
  cross-leverage limits. Missing or malformed account data blocks the entry.
  The same fresh position response must reconcile to active SQLite logical
  units before the approval is issued.
  The resulting approval matches one exact order and can only be consumed once.
- Entry placement has a separate process-local gate from the global live-order
  arm. SQLite entry control defaults to disabled. Enable and kill operations
  serialize with the full strategy submission/link lifecycle. Kill disables
  first and cancels only `pending_open` entry orders; reduce-only closes remain
  available. A partial cancellation failure is reported without re-enabling.
- Every default runtime acquires a non-expiring OS file lock for the normalized
  SQLite path before services run. Authenticated live account config must also
  include OKX `uid`; live mode additionally locks a hash of `{OKX_FLAG, uid}`.
  Conflict aborts startup before arming. Normal shutdown disarms orders before
  releasing locks; process death releases them through the OS without a TTL.
- Automatic signal/rule exits do not ask for human confirmation. Live startup,
  `MAYBECH_ARM_ORDERS=1`, the reduce-only client guard, and durable pre-submit
  audit must all succeed before an order is sent.
- Live entries require an explicit contract count for their instrument in the
  strategy record's `metadata.order_size_contracts`. Before entry or reduce-only
  close submission, Maybech fetches OKX metadata and rejects halted instruments,
  sizes below `minSz`, and sizes outside `lotSz`; limit and trigger prices are
  normalized to `tickSz` with decimal arithmetic.
- Every strategy entry carries its side-consistent absolute stop loss through
  OKX `attachAlgoOrds` on a FOK order. The persisted slippage cap sets the most
  aggressive allowed limit, and risk approval uses that worst-case price.
  Submission succeeds only after complete-fill and active-child verification.
  A compatible take profit is attached when configured; rapid-move and
  composite exits continue to be managed per logical unit.
- `POST /positions/logical/{position_id}/close` is the separate manual operator
  command and requires `{ "confirm": true }` to prevent accidental clicks.
- Rule deletion must be scoped by both `trade_id` and `group_id` so one trade
  cannot delete another trade's rule group.
- The confirmed-fill POST is a trusted local ingestion boundary, not an order
  placement endpoint. Keep it localhost-only until service authentication and
  authorization are implemented.
- REST fill polling remains the correctness/catch-up layer. Private order
  events reduce latency and report cancellations; reconnects still catch up
  through REST before new entries resume.
