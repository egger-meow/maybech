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
- `GET /events`
- `GET /account/snapshot`
- `GET /market/btc-regime`
- `GET /strategy/decisions`
- `GET /position/intents`
- `GET /strategies`
- `POST /strategies`
- `GET /strategies/{strategy_id}`
- `PATCH /strategies/{strategy_id}`
- `POST /strategies/{strategy_id}/enable`
- `POST /strategies/{strategy_id}/disable`
- `GET /strategies/{strategy_id}/signals`
- `POST /strategies/{strategy_id}/signals`
- `GET /positions/logical`
- `GET /positions/logical/{position_id}`
- `WS /ws/events`

See `docs/runtime-status.md` for current payload behavior.

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

The remaining target surface is:

- `POST /strategies/{strategy_id}/backtest`: run or schedule backtest.
- `GET /strategies/{strategy_id}/decisions`: list decision records with signal
  evidence, risk result, intended action, and action result.

## Target Signal Endpoints

Signals should be reusable by both strategies and position close rules:

- `GET /signals/templates`: list available primitive signal types and required
  parameters.
- `POST /signals/evaluate`: evaluate a signal expression against current or
  historical data without creating an action.
- `POST /signals/validate`: validate syntax and parameter ranges.

## Target Logical Position Endpoints

The Position Management page has a read-only logical-position contract now:

- `GET /positions/logical`: list current logical position units persisted in
  SQLite, with compatibility backfill from `TradeStore` records, attached close
  rules, current position intent, conservative reconciliation state against
  matching OKX net-position snapshots, and related audit events when available.
- `GET /positions/logical/{position_id}`: inspect one logical position unit.

The editable target surface remains:

- `PATCH /positions/logical/{position_id}/rules`: edit stop-loss, take-profit,
  break-even, trailing, and manual-review rules.
- `POST /positions/logical/{position_id}/close`: request close/reduce for one
  logical unit.
- `POST /positions/logical/{position_id}/break-even`: move or arm break-even
  handling for one logical unit.
- `GET /positions/groups`: list grouped summaries by instrument, side, strategy,
  or OKX net position.

## Target Visualization Endpoints

For clear K-line overlays in the UI:

- `GET /market/candles?inst_id=...&bar=...&limit=...`: recent candles.
- `GET /positions/logical/{position_id}/chart`: candles plus overlay levels for
  entry, current price, stop-loss, take-profit, break-even, and executed exits.

## Open Questions

- Whether signal expressions should be stored as JSON AST, a small DSL, or both.
- How strongly signal expressions should be validated before a strategy can be
  enabled.
- How execution-confirmation events should allocate partial fills, fees, and
  exchange close quantities to each logical unit after OKX exposes only an
  aggregate net position state.
- Whether manual positions should be imported automatically from OKX or created
  by explicit operator action.
- Which storage layer should become canonical for strategies, logical positions,
  and audit events: SQLite is the current likely next step.
