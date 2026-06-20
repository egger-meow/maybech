# Maybech

Maybech is a local-first crypto trading, monitoring, and backtesting workspace
for OKX perpetuals. The current system is a Python daemon runtime with a
FastAPI/WebSocket control surface, a Next.js dashboard, dynamic position rules,
BTC regime tracking, account snapshots, notifications, and backtesting tools.

This project should be treated as an operator-assist system first. Dry-run mode
is the default. Live strategy execution requires explicit `--live` startup and
the existing arming safeguards. Dynamic rule exits in `PositionManagerService`
do not place live close orders yet; in live mode they emit manual-close-required
events until a confirmed exchange close executor is implemented.

## Project Structure

- `run_api.py` starts daemon services behind the local FastAPI API.
- `run_services.py` starts daemon services without a UI.
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
- Local `.env` credentials for OKX/LINE/email integrations when needed.

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
uv run python run_api.py
```

Useful API endpoints:

- `GET http://127.0.0.1:8000/services`
- `GET http://127.0.0.1:8000/events`
- `GET http://127.0.0.1:8000/account/snapshot`
- `GET http://127.0.0.1:8000/market/btc-regime`
- `GET http://127.0.0.1:8000/strategy/decisions`
- `GET http://127.0.0.1:8000/position/intents`
- `ws://127.0.0.1:8000/ws/events`

Start daemon services without the API:

```powershell
uv run python run_services.py
```

Disable strategy execution for monitor-only service runs:

```powershell
uv run python run_services.py --no-strategy
```

## Frontend Dashboard

```powershell
cd frontend
npm install
npm run dev
```

The dashboard reads `NEXT_PUBLIC_API_URL`, defaulting to
`http://127.0.0.1:8000`.

Check frontend quality gates:

```powershell
npm run lint
npm run build
```

## Runtime Tracking

Use `docs/runtime-status.md` as the source of truth for API payloads, service
status keys, and current live-trading safety limits. Keep `toImprove.md`
maintained with at least three active improvement priorities.

For operational setup, see `docs/deployment.md`. For architecture direction,
see `docs/system-direction.md`.

## Security

Create secrets from `.env.example` when available and never commit `.env`.
Treat OKX API keys, LINE tokens, SMTP credentials, and notification targets as
sensitive. Keep the API bound to localhost unless authentication, TLS, and a
private access path are configured.
