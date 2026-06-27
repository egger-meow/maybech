# Maybech

Maybech is a local-first crypto trading, monitoring, and backtesting workspace
for OKX perpetuals. The current system is a Python daemon runtime with a
FastAPI/WebSocket control surface, a Next.js dashboard, dynamic position rules,
BTC regime tracking, account snapshots, notifications, and backtesting tools.

This project should be treated as an operator-assist system first. Dry-run mode
is the default. Live strategy execution requires explicit `--live` startup and
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
- `src/trading/` contains execution, risk, trade persistence, and dynamic rules.
- `src/market/`, `src/data/`, `src/backtesting/`, and `src/strategies/` contain
  market analysis, candle storage, simulation, and strategy logic.
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

## Run Locally

Start the API-backed runtime:

```powershell
uv run python -m src.runtime api
```

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
- `GET http://127.0.0.1:8000/strategies/momentum_swap/decisions`
- `GET http://127.0.0.1:8000/position/intents`
- `GET http://127.0.0.1:8000/execution/fills/status`
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
npm run build
```

`npm run contract` checks that `docs/openapi.json` and
`frontend/lib/generated/api-types.ts` still match the FastAPI OpenAPI schema.
Regenerate them from the repo root with:

```powershell
uv run python scripts/generate_openapi_types.py
```

## Runtime Tracking

Use `docs/runtime-status.md` as the source of truth for API payloads, service
status keys, and current live-trading safety limits. Keep `toImprove.md`
maintained with at least three active improvement priorities.

For product direction, see `docs/project-charter.md` and
`docs/domain-model.md`. For target API and UI shape, see `docs/api-spec.md` and
`docs/ui-direction.md`. For storage, schema migration, and SQLite direction,
see `docs/storage.md`. For operational setup, see `docs/deployment.md`. For
architecture direction, see `docs/system-direction.md`.

## Security

Create secrets from `.env.example` and never commit `.env`. The example file is
kept focused on active runtime/operator variables, including
`MAYBECH_ARM_ORDERS`, `MAYBECH_DB_PATH`, and `NEXT_PUBLIC_API_URL`. A
source-derived test rejects missing and obsolete entries.
Treat OKX API keys, LINE tokens, SMTP credentials, and notification targets as
sensitive. Keep the API bound to localhost unless authentication, TLS, and a
private access path are configured.
