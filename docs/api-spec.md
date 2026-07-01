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
- `GET /runtime/lease`
- `GET /runtime/capabilities`
- `GET /risk/limits`
- `PUT /risk/limits`
- `GET /risk/entries`
- `GET /instruments`
- `POST /instruments/refresh`
- `POST /risk/entries/enable`
- `POST /risk/entries/kill`
- `GET /events`
- `GET /audit/events`
- `GET /account/snapshot`
- `GET /account/exposure-reconciliation`
- `GET /market/btc-regime`
- `GET /market/candles`
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
- `POST /positions/import`
- `POST /positions/logical/{position_id}/protection`
- `GET /positions/logical/{position_id}`
- `GET /positions/logical/{position_id}/chart`
- `GET /positions/logical/{position_id}/allocations`
- `POST /positions/logical/{position_id}/allocations`
- `POST /positions/logical/{position_id}/close`
- `WS /ws/events`

See `docs/runtime-status.md` for current payload behavior.

`GET /runtime/preflight` reports whether startup checks passed, whether order
placement is armed, demo/real/dry-run mode, OKX account and position mode, and
the strategies/instruments validated before services started. Failed live
preflight aborts startup, so no unhealthy live API remains running.

`GET /runtime/lease` reports whether this process holds exclusive runtime
ownership, plus its process/host owner metadata, resolved database path,
optional hashed live-account scope, acquisition time, and lock directory. It
never exposes the raw OKX account UID or API credentials.

`GET /runtime/capabilities` identifies a `combined` execution leader versus a
read-only `replica`, along with mutation, runtime-control, live-snapshot,
storage, and authentication constraints. Replicas reject every non-read HTTP method before route
execution and reject the live-event WebSocket; mutations and in-memory runtime
traffic must be routed to the leader.

When `MAYBECH_API_TOKEN` is configured, all HTTP routes except `/health`,
`/runtime/capabilities`, and CORS preflight require a bearer token. The event
WebSocket accepts the token through its `token` query parameter. Non-loopback
startup additionally requires `--allow-remote`; a token never replaces TLS.

`GET /risk/limits` returns the singleton SQLite risk envelope. `PUT
/risk/limits` replaces it with explicit `enabled`, `max_order_notional_usd`,
`max_total_exposure_usd`, and `max_leverage` values. The order limit cannot
exceed the total exposure limit; all numeric limits must be positive.

`GET /instruments` returns the cached, currently tradable OKX SWAP instrument
metadata used by operator selectors and sizing conversion. An empty cache is a
visible `503`, never a hardcoded fallback. `POST /instruments/refresh` replaces
the SWAP cache atomically from the OKX public instruments API and returns the
same typed list contract. Records include contract value/currency, settlement
currency, lot/min/tick sizes, precision, state, refresh time, next refresh time,
and an explicit stale flag. The daemon refreshes the public catalog at startup
and before account ticks when the newest record is at least 24 hours old.
Sizing and manual-open mutations fail closed while the cache is stale.
`POST /instruments/{inst_id}/size-quote` converts an operator-facing base-asset
quantity into an exact OKX contract count from cached `ctVal`, `ctValCcy`,
`ctMult`, `lotSz`, and `minSz`. It also returns estimated USDT notional and an
optional side-aware rule-price PnL estimate. Missing, ambiguous, below-minimum,
or non-lot-aligned metadata returns a visible error and blocks submission.
`POST /instruments/{inst_id}/contract-quote` performs the reverse mapping for
persisted logical units, returning display quantity, notional, and optional
rule-price PnL without changing the stored OKX contract quantity.

Entry control is persisted separately from editable risk-limit values, so a
risk-limit update cannot silently re-enable trading. Entries default to
disabled, and every live startup persists them disabled again. Enable and kill
commands require `{ "confirm": true }`. Enable also requires a successfully
preflighted live process; dry-run or otherwise unarmed calls return `409` and
cannot schedule activation for a later restart. Kill disables the persisted and
process-local entry gates before resolving and canceling only Maybech
`pending_open` orders. It reports requested, already-requested,
already-terminal, unresolved, and error counts. Cancellation failures never
roll back the disabled state, and the entry gate does not affect reduce-only
position closes.

## Runtime And Audit Events

- `GET /events` returns the bounded, in-memory live event buffer.
- `WS /ws/events` streams live events to connected clients.
- `GET /audit/events` returns newest-first durable audit records from SQLite.
  It accepts `limit`, `event_type`, `source`, `strategy_id`, `correlation_id`,
  `position_id`, `trade_id`, and `before` filters. Position-manager
  close-condition evaluations, strategy execution lifecycles, and product API
  mutations are persisted. Definition mutations use `source=product_api` and
  include before/after snapshots where applicable. The definition change and
  its audit event commit in one SQLite transaction; an audit-write failure
  rolls the mutation back.

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
- `DELETE /strategies/{strategy_id}`: delete only a disabled strategy with no
  logical-position history; signal-expression children cascade with it.
- `GET /strategies/{strategy_id}/signals`: list persisted signal expressions.
- `POST /strategies/{strategy_id}/signals`: create a persisted signal
  expression.
- `GET /strategies/{strategy_id}/signals/{expression_id}`: inspect one child
  expression.
- `PATCH /strategies/{strategy_id}/signals/{expression_id}`: edit its purpose
  or validated JSON expression.
- `DELETE /strategies/{strategy_id}/signals/{expression_id}`: delete it.

Signal-expression edits and deletes automatically disable an enabled parent
strategy if the resulting execution contract is incomplete. Strategy and
signal mutations write durable audit evidence.

An executable strategy uses this persisted shape:

- `target_instruments`: OKX instruments to evaluate independently
- `entry_signal`: the primary JSON signal AST; `entry` and `filter` child
  expressions are combined with it using `and`
- `metadata.position_side`: `long` or `short`
- `metadata.candle_bar`: candle interval, defaulting to `1m`
- `metadata.order_size_contracts`: positive OKX contract counts keyed by target
- `metadata.max_entry_slippage_pct`: positive decimal fraction up to `0.05`;
- `execution_delay_seconds`: `0` for immediate execution, or up to `86400`.
  Positive delays require initial policy/risk approval before persisting a
  pending action, then rerun signal, policy, and risk checks at the due time
  before any submission.
  sets the worst acceptable FOK limit price and risk-check price
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

`execution_status=submitted` means OKX completely filled the slippage-capped FOK
parent and the exact attached protection is visible as an active algo. It does
not mean that the authenticated fill has already been allocated into SQLite.

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

- `POST /positions/manual-open`: create one `source=manual` logical unit from a
  cached instrument and operator-facing base quantity. The request requires an
  explicit confirmation and side-correct protective stop. In the current
  guarded implementation this endpoint accepts Dry-run only; demo/real modes
  return `409` and no new live-order path is enabled.
- `GET /account/exposure-reconciliation`: fetch fresh authenticated OKX SWAP
  positions and compare every instrument/side group with active SQLite logical
  units. Any exchange-only, missing, over-allocated, malformed, or unknown
  quantity makes `safe_for_entries=false`.
- `POST /positions/import`: with `{ "confirm": true }`, import exactly the
  current unexplained quantity for one instrument/side as a new independent
  logical unit. The caller cannot choose quantity or entry price. Import uses
  the fresh OKX gap and average price, requires a valid enabled side-consistent
  stop loss, and creates the unit plus close conditions atomically. It then
  places a quantity-scoped reduce-only conditional stop and verifies the exact
  pending OKX algo. Repeating an import after the gap is consumed returns `409`.
- `POST /positions/logical/{position_id}/protection`: with
  `{ "confirm": true }`, retry or reconcile protection for an
  imported/recovered/failed unit against its persisted stop condition. The unit
  remains entry-blocking unless exact pending-algo verification succeeds.
- `POST /positions/logical/{position_id}/protection/stop`: publish a confirmed
  stop edit. The request identifies the enabled stop-loss condition, supplies
  its replacement expression and reason, and requires `{ "confirm": true }`.
  The backend proves the old owned algo, persists and audits an amend intent,
  submits the exact size/price amendment, proves the resulting pending algo,
  and only then updates the close condition and protection record. Generic
  close-condition mutations return `409` when they would alter an owned stop.
  Verification also requires the protected quantity to equal the logical unit's
  current remaining quantity; matching a stale persisted size is not accepted.
- `POST /positions/logical/{position_id}/break-even`: with
  `{ "confirm": true }`, move the owned stop to entry or a side-consistent
  protected-profit offset. The command requires the enabled stop condition and
  a `lock_in_pct` from `0` through `0.05`. Current ticker price must already be
  beyond the directionally rounded target. The operation then uses the same
  durable, verified stop-amend lifecycle and stores break-even evidence on the
  condition and audit event.

- `GET /positions/logical`: list current logical position units persisted in
  SQLite, with compatibility backfill from `TradeStore` records, first-class
  close signal conditions, legacy trade rule groups during migration, current
  position intent, conservative reconciliation state against matching OKX
  net-position snapshots, and related audit events when available. Every unit
  also includes typed `protection` state with kind, status, OKX `algo_id`,
  `algo_client_order_id`, protected quantity, stop level, optional triggered
  child order id, timestamps, and lifecycle metadata.
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
- `POST /positions/logical/{position_id}/reduce`: explicitly confirmed partial
  reduce for an exact quantity smaller than the unit's current remainder. The
  unit atomically enters `reducing`, its owned stop is canceled before the
  reduce-only order is submitted, and quantity changes only from confirmed
  fills. Partial fills remain `reducing`; target completion or terminal
  cancellation returns the unit to `open` and restores exact protection for the
  confirmed remainder. Unknown submissions retain and retry one client order id.

Enabled close conditions and legacy rules do not call this manual endpoint and
do not wait for a person. In armed live mode, `PositionManagerService` submits
their reduce-only close automatically when the expression matches.

Before a live software/manual close is submitted, the position manager cancels
and proves absence of that unit's owned protective algo. A missing or ambiguous
algo blocks the close. Unknown close acceptance retains a retryable intent with
the same `clOrdId`; terminal or proven-missing closes with remaining quantity
re-arm protection before the unit returns to `open`.

A stop amendment temporarily enters `amending`. Response-loss recovery queries
the owned algo: a proved new stop completes normally, a proved old stop leaves
the old rule unchanged, and an ambiguous result marks protection `failed` and
disables entries. Background protection checks share the same execution lock so
they cannot observe a valid amendment halfway through.

`GET /positions/groups` returns typed persisted summaries grouped by
instrument/side, strategy plus instrument/side, or exchange-position key. Each
summary includes its logical-unit ids, status counts, active count, total
opened/remaining quantity, and remaining-quantity-weighted entry price. The
`limit` applies to complete groups after all matching positions are aggregated;
it never truncates a group's financial totals.

Create, edit, and delete operations for logical-position close conditions also
write durable `product_api` audit events with condition snapshots.

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
eventual exchange order ID, stale client intents recovered, protection checks,
protection triggers linked, protection re-arms, protection errors, and update
time.
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

## Visualization Endpoints

The backend exposes the data required for clear K-line overlays in the UI:

- `GET /market/candles?inst_id=...&bar=...&limit=...`: recent candles.
- `GET /positions/logical/{position_id}/chart`: candles plus overlay levels for
  entry, current price, stop-loss, take-profit, break-even, and executed exits.

Candle rows are ascending typed OHLCV values with confirmed/unconfirmed state.
Position overlays are derived from the independent logical unit's persisted
entry, enabled close conditions, break-even evidence, and confirmed allocation
records rather than the merged OKX position.

## Open Questions

- Whether signal expressions should be stored as JSON AST, a small DSL, or both.
- How ambiguous externally initiated reductions should be allocated when OKX
  exposes only aggregate net position state and no matching Maybech order id.
- Whether manual positions should be imported automatically from OKX or created
  by explicit operator action.
- What retention windows and compaction rules should apply to high-volume audit
  records.
