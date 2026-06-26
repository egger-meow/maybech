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
- `GET /strategies` returns a typed read-only strategy summary for the current
  momentum strategy, including runtime service status, signal parameters,
  target instruments, default rule parameters, and latest decisions.
- `GET /positions/logical` returns typed read-only logical position units from
  `LogicalPositionStore`, with compatibility backfill from `TradeStore` records.
  These include attached close conditions, current position intent, matching OKX
  net-position snapshot data when present, conservative reconciliation state,
  and related audit events when event payloads reference the trade/position id.
- `GET /events` returns recent in-memory runtime events.
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
and conservative reconciliation against OKX net-position snapshots. It is not
yet the final execution-confirmed allocation lifecycle described in
`docs/domain-model.md`.

## Safety Notes

- Live position-manager rule exits are blocked until an exchange close-order
  executor is implemented and confirmed. Dry-run mode may still close simulated
  trades in `TradeStore`.
- Rule deletion must be scoped by both `trade_id` and `group_id` so one trade
  cannot delete another trade's rule group.
