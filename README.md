# Maybech

Maybech is a local-first crypto trading and monitoring workspace
for OKX perpetuals. The current system is a Python daemon runtime with a
FastAPI/WebSocket control surface, a Next.js dashboard, dynamic position rules,
BTC regime tracking, account snapshots, notifications, and persisted signals.

This project should be treated as an operator-assist system first. `simulation`
is the default. The runtime modes are `simulation`, `demo`, `live_safe`, and
`live_armed`; `--live` remains a deprecated compatibility alias. Live strategy
execution requires explicit `--mode live_armed` startup and
the existing arming safeguards. In armed live mode, triggered close conditions
automatically submit reduce-only market orders. Logical quantity and trade state
remain unchanged until authenticated OKX fills confirm the exit.

## Project Structure

- `src/runtime/` owns CLI parsing and startup for API and headless service
  modes.
- `run_api.py` and `run_services.py` are compatibility wrappers around
  `src/runtime/`.
- `src/api/` contains HTTP/WebSocket endpoints and Pydantic schemas.
- `src/daemon/` contains background services and runtime state.
- `src/trading/` contains signal evaluation, strategy execution, persistence,
  logical positions, and dynamic close rules.
- `src/market/` and `src/data/` contain market analysis and candle storage.
- `frontend/` contains the Next.js dashboard.
- `docs/` contains architecture, deployment, and runtime tracking notes.
- `toImprove.md` tracks the current top improvement priorities.

## Requirements

- Python 3.13 recommended. `pyproject.toml` supports `>=3.11,<3.15`.
- `uv` for Python environment and dependency management.
- Node.js and npm for the `frontend/` dashboard.
- Local `.env` copied from `.env.example` for OKX/LINE/email integrations when
  needed.

`requirements.txt` is currently the canonical dependency list for the Python
runtime. `pyproject.toml` holds project metadata and pytest configuration until
dependencies are consolidated into project metadata.

Install `uv` on Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv --version
```

## Backend Setup

```powershell
uv python install 3.13
uv venv --python 3.13
.venv\Scripts\activate
uv pip install -r requirements.txt
```

Run tests:

```powershell
.venv\Scripts\python.exe -m pytest
```

`uv run pytest` is also valid when the uv cache is accessible. On this Windows
workspace, using the activated `.venv` avoids local uv cache permission issues.

Real OKX read-only integration tests are opt-in. Run them only with outbound
network access and the intended Demo/production credential set:

```powershell
$env:MAYBECH_RUN_OKX_INTEGRATION = "1"
.venv\Scripts\python.exe -m pytest tests/test_okx_integration.py -v
```

This flag enables network/API assertions only; network, authentication, and API
errors remain real test failures. Order submission, cancellation, and protective
order mutation remain blocked. An intentional Demo execution test additionally
requires `MAYBECH_RUN_OKX_DEMO_EXECUTION=1` with `OKX_FLAG=1`. The test suite
never enables production order mutation, and runtime live arming remains a
separate safety gate.

## Run Locally

Start the API-backed runtime:

```powershell
uv run python -m src.runtime api
uv run python -m src.runtime api --mode demo
uv run python -m src.runtime api --mode live_safe
uv run python -m src.runtime api --mode live_armed
uv run python -m src.runtime api --role replica
```

The API binds to loopback by default. A non-loopback host requires both
`--allow-remote` and `MAYBECH_API_TOKEN`; use TLS at a reverse proxy or private
tunnel because bearer authentication alone does not encrypt traffic.

Compatibility wrapper:

```powershell
uv run python run_api.py
```

Useful API endpoints:

- `GET http://127.0.0.1:8000/services`
- `GET http://127.0.0.1:8000/events`
- `GET http://127.0.0.1:8000/audit/events`
- `GET http://127.0.0.1:8000/account/snapshot`
- `GET http://127.0.0.1:8000/market/btc-regime`
- `GET http://127.0.0.1:8000/strategy/decisions`
- `GET http://127.0.0.1:8000/strategies/{strategy_id}/decisions`
- `GET http://127.0.0.1:8000/position/intents`
- `GET http://127.0.0.1:8000/execution/fills/status`
- `GET http://127.0.0.1:8000/runtime/preflight`
- `GET http://127.0.0.1:8000/runtime/lease`
- `GET/PUT http://127.0.0.1:8000/risk/limits`
- `GET http://127.0.0.1:8000/risk/entries`
- `POST http://127.0.0.1:8000/risk/entries/enable`
- `POST http://127.0.0.1:8000/risk/entries/kill`
- `GET http://127.0.0.1:8000/positions/logical`
- `ws://127.0.0.1:8000/ws/events`

Start daemon services without the API:

```powershell
uv run python -m src.runtime services
```

Disable strategy execution for monitor-only service runs:

```powershell
uv run python -m src.runtime services --no-strategy
```

Order-capable Demo or Live Armed execution requires its explicit `--mode`, valid OKX
credentials, `MAYBECH_ARM_ORDERS=1`, a derivatives-capable account in
`net_mode`, enabled SQLite account risk limits, and passing strategy/instrument
checks. Startup aborts before daemon
threads run if any preflight check fails. `OKX_FLAG=1` targets demo trading;
`OKX_FLAG=0` targets real trading.
Demo mode reads only `DEMO_OKX_API_KEY`, `DEMO_OKX_API_SECRET`, and
`DEMO_OKX_PASSPHRASE`. Production mode reads only the corresponding unprefixed
`OKX_*` variables, preventing endpoint mode and credential-set mixing.
Live startup also requires authenticated subscription to the OKX private
`orders/SWAP` channel. New strategy entries stay blocked until durable REST
fill catch-up is current and that stream is connected; reduce-only closes stay
active independently.
Every default runtime acquires an OS-held lock for its resolved SQLite path so
dry and live processes cannot mutate the same state concurrently. Live mode
also locks a hashed demo/real OKX account scope. A second process targeting
either resource exits before services run or orders are armed. Locks release
automatically on process death and explicitly after order placement is disarmed.
Strategy entries default to disabled until an operator enables them. Enabling entries
requires a successfully preflighted order-capable process plus `{ "confirm": true }`;
Simulation or offline enable attempts are rejected instead of being scheduled for restart.
Once enabled, the gate stays open for the current running process so multiple new
positions do not require repeated confirmation. Every real runtime startup resets
the entry gate to disabled, even if the previous process ended while entries were
enabled. Killing entries requires the same confirmation and stays disabled until
explicitly re-enabled.
The kill command persists the disabled state before canceling Maybech
`pending_open` orders and never disables reduce-only close submission.

Compatibility wrapper:

```powershell
uv run python run_services.py
```

## Frontend Dashboard

```powershell
cd frontend
npm install
npm run dev
```

The dashboard reads `NEXT_PUBLIC_API_URL`, defaulting to
`http://127.0.0.1:8000`.
FastAPI accepts browser requests from the comma-separated local origins in
`MAYBECH_CORS_ORIGINS`.

Check frontend quality gates:

```powershell
npm run contract
npm run lint
npm run typecheck
npm run build
```

Run all four frontend gates in order with `npm run verify`.

`npm run contract` checks that `docs/openapi.json` and
`frontend/lib/generated/api-types.ts` still match the FastAPI OpenAPI schema.
Regenerate them from the repo root with:

```powershell
uv run python scripts/generate_openapi_types.py
```

## Runtime Tracking

Use `docs/runtime-status.md` as the source of truth for API payloads, service
status keys, and current live-trading safety limits. Keep `toImprove.md`
limited to concrete current real-money blockers; do not add replacement items
merely to maintain a fixed count.

For product direction, see `docs/project-charter.md` and
`docs/domain-model.md`. For target API and UI shape, see `docs/api-spec.md` and
`docs/ui-direction.md`. For storage, schema migration, and SQLite direction,
see `docs/storage.md`. For operational setup, see `docs/deployment.md`. For
architecture direction, see `docs/system-direction.md`.

## Release Version

The current release baseline is `v0.1.0`. Project version metadata is stored in
`pyproject.toml`, `src/version.py`, `frontend/package.json`, and
`frontend/package-lock.json`. GitHub releases should use annotated tags such as
`v0.1.0`; see `docs/release.md` for the release checklist and notes template.

## Security

Create secrets from `.env.example` and never commit `.env`. The example file is
kept focused on active runtime/operator variables, including
`MAYBECH_ARM_ORDERS`, `MAYBECH_DB_PATH`, and `NEXT_PUBLIC_API_URL`. A
source-derived test rejects missing and obsolete entries.
Treat OKX API keys, LINE tokens, SMTP credentials, and notification targets as
sensitive. Notification transports are reserved for strategy, position,
runtime-safety, and exchange/API lifecycle events; standalone support/resistance
price alerts are removed. Keep the API bound to localhost unless authentication,
TLS, and a private access path are configured.
