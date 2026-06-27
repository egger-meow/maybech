# API Spec Direction

This file records the intended API contract. Keep `docs/runtime-status.md` in
sync with endpoints that already exist, and use this file to design the durable
surface before frontend/backend work expands.

## Contract Goals

- Use typed Pydantic response models for all API payloads.
- Generate or validate frontend TypeScript types from the backend contract.
- Preserve explicit state names for strategy, signal, and logical position
  lifecycles.
- Separate exchange net positions from Maybech logical position units.
- Include audit/evidence fields so UI pages can explain why actions happened.

## Existing Snapshot Endpoints

These endpoints currently exist or are already documented in runtime status:

- `GET /services`
- `GET /runtime/preflight`
- `GET /risk/limits`
- `PUT /risk/limits`
- `GET /events`
- `GET /audit/events`
- `GET /account/snapshot`
- `GET /market/btc-regime`
- `GET /strategy/decisions`
- `GET /strategies/{strategy_id}/decisions`
- `GET /position/intents`
- `GET /execution/fills/status`
- `GET /strategies`
- `POST /strategies`
- `GET /strategies/{strategy_id}`
- `PATCH /strategies/{strategy_id}`
- `POST /strategies/{strategy_id}/enable`
- `POST /strategies/{strategy_id}/disable`
- `GET /strategies/{strategy_id}/signals`
- `POST /strategies/{strategy_id}/signals`
- `GET /signals/templates`
- `GET /signals/context`
- `POST /signals/validate`
- `POST /signals/evaluate`
- `GET /positions/logical`
- `GET /positions/logical/{position_id}`
- `GET /positions/logical/{position_id}/allocations`
- `POST /positions/logical/{position_id}/allocations`
- `POST /positions/logical/{position_id}/close`
- `WS /ws/events`

See `docs/runtime-status.md` for current payload behavior.

`GET /runtime/preflight` reports whether startup checks passed, whether order
placement is armed, demo/real/dry-run mode, OKX account and position mode, and
the strategies/instruments validated before services started. Failed live
preflight aborts startup, so no unhealthy live API remains running.

`GET /risk/limits` returns the singleton SQLite risk envelope. `PUT
/risk/limits` replaces it with explicit `enabled`, `max_order_notional_usd`,
`max_total_exposure_usd`, and `max_leverage` values. The order limit cannot
exceed the total exposure limit; all numeric limits must be positive.

## Runtime And Audit Events

- `GET /events` returns the bounded, in-memory live event buffer.
- `WS /ws/events` streams live events to connected clients.
- `GET /audit/events` returns newest-first durable audit records from SQLite.
  It accepts `limit`, `event_type`, `source`, `strategy_id`, `correlation_id`,
  `position_id`, `trade_id`, and `before` filters. Position-manager
  close-condition evaluations and strategy execution lifecycles are currently
  persisted.

The durable endpoint is the restart-safe source for operator history. It does
not yet persist every runtime event and has no retention/compaction contract.

## Generated Contract Files

The current OpenAPI schema is checked in at `docs/openapi.json`. Frontend schema
types are generated at `frontend/lib/generated/api-types.ts`, re-exported from
`frontend/lib/api.ts`, and used by typed dashboard API helpers.

Regenerate from the repo root:

```powershell
uv run python scripts/generate_openapi_types.py
```

Check for drift:

```powershell
uv run python scripts/generate_openapi_types.py --check
cd frontend
npm run contract
```

## Target Strategy Endpoints

The Strategy Management page has a persisted strategy definition contract now:

- `GET /strategies`: list the current strategy summary, runtime service state,
  target instruments, signal parameters, default rules, and latest decisions.
- `GET /strategies/{strategy_id}`: inspect one strategy summary.
- `POST /strategies`: create a strategy with entry signal expression and
  default position rules.
- `PATCH /strategies/{strategy_id}`: edit strategy metadata, signal expression,
  risk filters, and default position rules.
- `POST /strategies/{strategy_id}/enable`: mark a persisted strategy enabled.
- `POST /strategies/{strategy_id}/disable`: mark a persisted strategy disabled.
- `GET /strategies/{strategy_id}/signals`: list persisted signal expressions.
- `POST /strategies/{strategy_id}/signals`: create a persisted signal
  expression.

An executable strategy uses this persisted shape:

- `target_instruments`: OKX instruments to evaluate independently
- `entry_signal`: the primary JSON signal AST; `entry` and `filter` child
  expressions are combined with it using `and`
- `metadata.position_side`: `long` or `short`
- `metadata.candle_bar`: candle interval, defaulting to `1m`
- `metadata.order_size_contracts`: positive OKX contract counts keyed by target
- `default_rules.close_conditions`: enabled close-condition objects copied to
  every logical position unit created by the strategy

At least one enabled `stop_loss` must be an absolute, side-consistent self-price
condition (`price_below` for long, `price_above` for short). It is attached to
the OKX entry as exchange-side protection. A compatible `take_profit` is also
attached when configured; other conditions remain software-managed per unit.

Child expressions with purpose `exit` are also copied to each new logical unit
as enabled generic exit conditions.

Create and patch requests cannot leave a strategy enabled unless this complete
execution contract validates. Editing an enabled strategy into an invalid state
disables it. Runtime evaluation is edge-triggered through SQLite, so a condition
that remains true across ticks or restarts does not repeatedly add exposure.

The remaining target surface is:

- `POST /strategies/{strategy_id}/backtest`: run or schedule backtest.
- `GET /strategies/{strategy_id}/decisions`: list newest-first durable decision
  records with policy evidence, execution status/result, order/trade/position
  references, and a shared correlation id. It accepts `limit`, `allowed`,
  `execution_status`, and `before` filters.

`execution_status=submitted` means the exchange order request returned a
non-empty response. It does not mean that OKX confirmed a fill or that logical
position allocation is final.

## Target Signal Endpoints

Signals should be reusable by both strategies and position close rules:

- `GET /signals/templates`: list available primitive signal types and required
  parameters.
- `GET /signals/context`: return the current signal evaluation context. By
  default this is derived from BTC regime and account position snapshots; with
  `include_candles=true`, `symbols`, `bar`, and `candle_limit`, it also includes
  recent candle-derived latest prices, rapid-move change percentages, and
  volume ratios.
- `POST /signals/evaluate`: evaluate a signal expression against current or
  historical data without creating an action.
- `POST /signals/validate`: validate syntax and parameter ranges.

The current implementation supports JSON expression objects:

- primitives such as `price_above`, `price_below`, `rapid_drop`,
  `rapid_rise`, and `volume_multiple`
- composites shaped as `{ "op": "and" | "or", "conditions": [...] }`

Persisted strategies must satisfy the complete execution contract before
`POST /strategies/{strategy_id}/enable` succeeds.
`POST /signals/evaluate` can use caller-provided context, or merge in runtime
context with `use_runtime_context=true`. It can also fetch candle-derived
context with `use_candle_context=true`; the evaluator derives required symbols
and rapid-move windows from the expression, and caller-provided context still
overrides generated values.

## Target Logical Position Endpoints

The Position Management page has a logical-position contract now:

- `GET /positions/logical`: list current logical position units persisted in
  SQLite, with compatibility backfill from `TradeStore` records, first-class
  close signal conditions, legacy trade rule groups during migration, current
  position intent, conservative reconciliation state against matching OKX
  net-position snapshots, and related audit events when available.
- `GET /positions/logical/{position_id}`: inspect one logical position unit.
- `GET /positions/logical/{position_id}/allocations`: list typed confirmed fill
  allocations for one unit.
- `POST /positions/logical/{position_id}/allocations`: ingest one confirmed
  open/reduce/close fill. The request requires a stable fill id, positive
  quantity and price, confirmation source, and an exchange order id for OKX
  fills. Identical retries are idempotent; conflicting reuse of a fill id is
  rejected with `409`.
- `GET /positions/logical/{position_id}/close-conditions`: list persisted
  stop-loss, take-profit, trailing, break-even, manual-review, and exit signal
  expressions for one logical unit.
- `POST /positions/logical/{position_id}/close-conditions`: create a validated
  close signal expression for one logical unit.
- `PATCH /positions/logical/{position_id}/close-conditions/{condition_id}`:
  edit purpose, expression, enabled state, or metadata.
- `DELETE /positions/logical/{position_id}/close-conditions/{condition_id}`:
  remove one close signal condition.
- `POST /positions/logical/{position_id}/close`: explicitly confirmed manual
  operator close. It delegates to the same reduce-only submission and confirmed
  fill lifecycle as automatic exits.

Enabled close conditions and legacy rules do not call this manual endpoint and
do not wait for a person. In armed live mode, `PositionManagerService` submits
their reduce-only close automatically when the expression matches.

The editable target surface remains:

- `POST /positions/logical/{position_id}/break-even`: move or arm break-even
  handling for one logical unit.
- `GET /positions/groups`: list grouped summaries by instrument, side, strategy,
  or OKX net position.

The allocation POST does not place an order and must not be exposed as an
unauthenticated public mutation. Production callers should be the authenticated
OKX fill-ingestion service or an explicit operator recovery workflow. The
runtime currently polls authenticated SWAP fills every five seconds for
restart-safe catch-up. Each matched fill updates quantity and weighted entry
price, records fees, and follows its correlation id back to the strategy
decision. Unknown exchange orders are counted as unmatched and never assigned
to a logical unit automatically.

`GET /execution/fills/status` exposes the latest polling counts: fetched,
applied, idempotent, unmatched, invalid, conflicts, orders checked, terminal
recoveries, stale cancellation requests, filled orders awaiting allocation,
deduplicated missing-fill alerts, order errors, client orders linked to an
eventual exchange order ID, stale client intents recovered, and update time.
It also reports durable catch-up state: pages fetched, `caught_up`, whether a
cursor cycle is in progress, history exhaustion, committed high-water bill ID,
next `after` bill ID, and cursor errors. A committed high-water mark never moves
past a page that was not fully ingested or durably quarantined.
It also exposes private order-stream connectivity, cumulative received events,
events processed in the latest daemon cycle, WebSocket-applied fills and
terminal recoveries, reconnect/drop counts, and latest message/error details.
The WebSocket is a latency path only; REST catch-up remains authoritative, and
new strategy entries require both a current REST cursor and a connected stream.

Logical position responses expose both `client_order_id` and
`exchange_order_id`. The client ID exists before submission and remains until
the order reaches a completed or safely recovered state.

## Target Visualization Endpoints

For clear K-line overlays in the UI:

- `GET /market/candles?inst_id=...&bar=...&limit=...`: recent candles.
- `GET /positions/logical/{position_id}/chart`: candles plus overlay levels for
  entry, current price, stop-loss, take-profit, break-even, and executed exits.

## Open Questions

- Whether signal expressions should be stored as JSON AST, a small DSL, or both.
- How ambiguous externally initiated reductions should be allocated when OKX
  exposes only aggregate net position state and no matching Maybech order id.
- Whether manual positions should be imported automatically from OKX or created
  by explicit operator action.
- What retention windows and compaction rules should apply to high-volume audit
  records.
