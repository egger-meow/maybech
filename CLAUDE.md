# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Maybech is a local-first, signal-driven crypto trading and monitoring system for OKX
perpetuals: a Python daemon runtime (FastAPI/WebSocket control surface) plus a Next.js
dashboard. It is an operator-assist system, not an autonomous trading bot — `simulation`
is the default runtime mode, and live order placement requires explicit arming.

## Commands

### Backend (Python, `uv`, Python 3.13 recommended)

```powershell
uv python install 3.13
uv venv --python 3.13
.venv\Scripts\activate
uv pip install -r requirements.txt

uv run python -m src.runtime api             # FastAPI/WebSocket runtime for the dashboard
uv run python -m src.runtime api --mode demo # demo | simulation | live_safe | live_armed
uv run python -m src.runtime services         # daemon services with no API/UI
uv run python -m src.runtime services --no-strategy  # monitor-only, no strategy execution

uv run pytest                 # full suite (also: .venv\Scripts\python.exe -m pytest)
uv run pytest --cov=src       # with coverage; use when touching strategy/exchange/daemon code
uv run pytest tests/test_daemon_service.py -v      # single file
uv run pytest tests/test_daemon_service.py::test_x -v  # single test
```

`run_api.py` / `run_services.py` are thin compatibility wrappers around `src/runtime/`
— prefer `-m src.runtime {api,services}` for new work.

Real OKX read-only integration tests are opt-in and require network + credentials:

```powershell
$env:MAYBECH_RUN_OKX_INTEGRATION = "1"
.venv\Scripts\python.exe -m pytest tests/test_okx_integration.py -v
```

Order submission/cancellation/protective-order mutation is never enabled by the test
suite; an intentional demo execution test additionally needs
`MAYBECH_RUN_OKX_DEMO_EXECUTION=1` with `OKX_FLAG=1`.

### Frontend (`frontend/`, Next.js)

```powershell
cd frontend
npm install
npm run dev        # next dev --webpack

npm run contract   # docs/openapi.json + generated api-types.ts match FastAPI schema
npm run lint
npm run typecheck
npm run build
npm run verify     # runs the four gates above, in order
```

Regenerate the OpenAPI contract/types from the repo root after changing FastAPI schemas:

```powershell
uv run python scripts/generate_openapi_types.py
```

`frontend/AGENTS.md` warns this is a newer, breaking-change Next.js version — check
`node_modules/next/dist/docs/` before relying on training-data Next.js APIs.

## Architecture

### Runtime modes are the central concept

`src/runtime/mode.py` defines `RuntimeMode`: `simulation` (default), `demo`, `live_safe`,
`live_armed`. Exchange connectivity (`touches_exchange`) and order permission
(`submits_orders`) are independent axes — e.g. `live_safe` connects to production for
inspection/recovery but never submits orders. `--live` is a deprecated alias resolved via
`OKX_FLAG`. Demo and live-armed use **disjoint credential namespaces**
(`DEMO_OKX_*` vs `OKX_*`) so endpoint and credential set can never mix.

### Startup / daemon composition

`src/daemon/runtime.py::create_default_runner` is the single factory every entrypoint
(API, headless services, tests) goes through. Order of operations matters:

1. Disarm order placement, acquire an OS-held file lock on the resolved SQLite path
   (`RuntimeLease`) — a second process targeting the same DB (or, in live mode, the same
   hashed OKX account scope) exits before touching state.
2. Simulation gets a canned preflight report; non-simulation runs
   `src/runtime/live_preflight.py`, which checks strategy/instrument config, risk limits,
   and (for order-capable modes) forces entry-control disabled at startup.
3. Services are registered conditionally on mode: `AccountSnapshotService` and
   `ExecutionFillService` only outside simulation, `PositionManagerService` skipped in
   `live_safe`, `StrategyService` only if preflight passed. `LifecycleNotificationService`
   and `BTCRegimeService`/`PositionIntentService` always run.
4. `runner.setup_services(required_services=...)` — the required set depends on mode —
   and only then is order placement armed (`arm_order_placement`), and only if the mode
   submits orders and preflight passed.

Any exception during this sequence disarms orders and releases the lease/services before
re-raising — startup is fail-closed, not best-effort.

Live entry (new positions) additionally requires the OKX private `orders/SWAP` channel to
be authenticated and connected, and REST fill catch-up to be current; reduce-only closes
stay available independently of that gate. Strategy entries default to disabled until an
explicit `{"confirm": true}` enable call against an already-preflighted order-capable
process; after that, the enabled/disabled state is persisted and restored across restarts
(once the process is armed again) instead of being force-reset every startup. `POST
/risk/entries/kill` still disables entries immediately and stays disabled until explicitly
re-enabled.

### Package layout (`src/`)

- `runtime/` — CLI parsing, mode resolution, live preflight, the SQLite-path lease, and
  the FastAPI app entrypoint (`api_server.py`). This is the only place that should own
  startup sequencing.
- `daemon/` — long-running services (`service.py: DaemonRunner` is the scheduler/registry)
  plus one file per service (account snapshots, BTC regime, execution fills, position
  intents/manager, strategy, lifecycle notifications).
- `api/` — FastAPI endpoints (`app.py`) and Pydantic schemas (`schemas.py`); read-only
  `replica` role connections open SQLite with `mode=ro` + `PRAGMA query_only` and skip
  schema init, enforced at the DB layer in addition to HTTP method filtering.
- `trading/` — the domain core: signal evaluation (`signal_engine.py`), strategy execution
  (`strategy_runtime.py`, `executor.py`), logical position units and their close rules
  (`logical_position_store.py`, `position_rule_model.py`, `rules.py`), risk
  (`account_risk.py`, `entry_control.py`), execution fill allocation
  (`execution_allocation.py`, `execution_cursor_store.py`, `execution_health.py`), and all
  SQLite stores (each with a schema version — see below).
- `exchange/` — OKX REST/WebSocket access.
- `data/` / `market/` — candle storage, indicators, BTC regime analysis.
- `notifications/` — LINE/email alert delivery.
- `monitor/` — account inspection helpers.
- `config/` — runtime defaults/settings; keep credentials and account-specific values out
  of code, use `.env` (from `.env.example`) instead.

### Domain model to know before changing behavior

- **Logical position units**, not raw OKX positions, are the unit of control. OKX may
  merge repeated same-side entries into one exchange position; Maybech must still track
  each open/add action as an independently-ruled unit (own stop-loss/take-profit/
  break-even/reduce/close), optionally tagged with the strategy that created it.
  See `docs/domain-model.md` and `docs/system-direction.md`.
- **BTC regime** (`BTCRegimeService`) is a first-class market-state input consumed by
  strategy and position-action policy (`BTCRegimeActionPolicy`) before any entry — BTC is
  treated as a risk/regime signal, not just another tradable pair.
- **Strategy decisions** are durable: every false-to-true signal edge becomes a structured
  `strategy.action_decision` audit event (BTC direction/strength/impulse, entry price,
  allow/block reason, evidence, correlation id), queryable via
  `GET /strategy/decisions` (latest) and `GET /strategies/{id}/decisions` (history).
- **Confirmed fills** are the source of truth for logical-quantity/state changes — even in
  armed live mode, a triggered close submits a reduce-only order but the unit stays
  `closing` until an authenticated OKX fill confirms it.

### SQLite persistence rules (`docs/storage.md`)

- Default DB path is `data/trades.db` via `MAYBECH_DB_PATH`, used by Simulation, Live Safe,
  and Live Armed; `--mode demo` instead uses `DEMO_MAYBECH_DB_PATH` (`data/demo_trades.db`
  by default), mirroring the `DEMO_OKX_*`/`OKX_*` credential split so Demo state can never
  land in the Simulation/Live database. `create_default_runner` resolves and pins the active
  path once at startup via `src/config/settings.py::activate_db_path`; all stores go through
  `src/trading/sqlite_schema.py` for connection config and the shared `schema_migrations`
  ledger.
- Every table-owning store needs a versioned schema entry — don't add
  `CREATE TABLE IF NOT EXISTS` without recording a version.
- Confirmed allocation inserts + their logical-position quantity changes must commit in
  one transaction; external fill ids are immutable idempotency keys (conflicting payloads
  must fail, never overwrite). Product-definition mutations and their audit events must
  likewise commit atomically.
- Runtime file locks (`RuntimeLease`) are ownership metadata only, separate from schema
  migration state.

### When retiring legacy strategy paths

Per `AGENTS.md`: once the persisted signal-based strategy model covers a legacy strategy
path, remove the superseded runtime code, config, tests, docs, and hardcoded frontend
references outright — do not keep parallel implementations as permanent compatibility
fallbacks.

## Key references

- `AGENTS.md` — repo guidelines (structure, style, testing, commit conventions).
- `toImprove.md` — the current live priority queue for real-money-safety blockers; treat
  it as an ordered contract, not a backlog, before starting safety/risk-adjacent work.
- `docs/README.md`, `docs/project-charter.md`, `docs/domain-model.md` — product concepts.
- `docs/system-direction.md` — target architecture and refactor priorities.
- `docs/runtime-status.md` — source of truth for API payloads, service status keys, and
  current live-trading safety limits.
- `docs/api-spec.md` / `docs/ui-direction.md` — target API and dashboard shape.
- `docs/storage.md` / `docs/deployment.md` — schema/migration and operational notes.
