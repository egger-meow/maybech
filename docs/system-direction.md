# Maybech System Direction

## Product Goal

Maybech should be a robust auto-signaling and action-trigger system for perpetual position management. It is not primarily a high-frequency or fully autonomous quant trading bot. The core loop should detect market state, produce explainable signals, manage position actions safely, and present a precise, vivid, interactive control surface.

## Current Fit

The current codebase is a good Python MVP:

- `src/daemon/` already separates background services from the UI.
- `StrategyService` and `NotificatorService` can run continuously.
- `src/ui/` provides a local Textual dashboard for control and inspection.
- OKX order placement has safety guards through dry-run and explicit arming.

The main limitation is process coupling. The Textual UI starts the same `DaemonRunner` in-process, and status is partly shared through direct object access or JSON files. That works locally, but it is not ideal for a web frontend, instant updates, or long-running production behavior.

## Recommended Architecture

Keep Python as the backend engine. Trading, OKX access, candle mining, signal generation, risk checks, and execution should stay in Python because the existing ecosystem and code are already there.

Add a backend API layer before moving to a richer frontend:

```text
OKX REST/WebSocket -> Market Data Service -> Signal Engine -> Risk/Action Engine
                                      |              |              |
                                      v              v              v
                                Event Store     Notifications   Execution Log
                                      |
                                      v
                         FastAPI HTTP + WebSocket API
                                      |
                         Textual UI or Web Frontend
```

The first Python API boundary now exists through `run_api.py` and `src/api/app.py`.
It exposes:

- service status and enable/disable controls
- recent runtime events
- a WebSocket stream for live frontend updates
- generated signal and notification events published by daemon services
- account summary, open positions, and recent order snapshots
- current BTC-led market regime
- strategy action decisions with allow/block reasons
- position-management intents for existing perpetual positions

The next API expansion should add richer decision records with signal reasons,
risk checks, and action results, plus editable operator review states for
manual position management.

After that, a web UI can be added. Next.js is fine if the UI becomes a full browser dashboard, but a lighter Vite/React frontend may be faster and simpler for a local control panel. Textual remains useful for SSH/local operator mode.

## BTC-Led Strategy Model

The system should treat Bitcoin as a market regime input, not just another trading pair. Add a dedicated BTC market state service that publishes:

- trend direction and strength
- sudden impulse or volatility expansion
- key level proximity
- correlation/risk mode for altcoin positions

Other strategies should consume this BTC state before opening, closing, reducing, or blocking positions.

The first version is implemented as `BTCRegimeService`. It publishes
`market.btc_regime` events and stores the latest regime in runtime state for
`GET /market/btc-regime`. The next step is to make strategy and risk logic
consume this state explicitly before action execution.

Strategy execution now uses `BTCRegimeActionPolicy` before placing any setup.
Each non-HOLD signal becomes a structured `strategy.action_decision` event with
the BTC direction, strength, impulse, setup prices, and allow/block reason. The
API exposes the latest decision snapshot at `GET /strategy/decisions`.

Open positions also flow through `PositionIntentService`, which emits
`position.intents` snapshots from the latest account state and BTC regime. That
gives the frontend a direct feed for hold/reduce/close/manual-review guidance.

## Always-On Deployment

For a personal always-running PC setup, two stages make sense:

1. Short term: run the Python daemon at OS startup with Windows Task Scheduler.
2. Medium term: run backend services in Docker Compose.

Docker is recommended once the API layer exists. Use separate containers for backend, frontend, and optional storage. Do not expose OKX-control endpoints publicly through port forwarding without authentication, TLS, and strict allowlists. Prefer local-only access, VPN/Tailscale, or reverse proxy authentication.

This repo now includes a conservative `Dockerfile` and `docker-compose.yml` for the API runtime. The compose file binds the API to localhost only. See `docs/deployment.md` for the operational notes.

## Refactor Priorities

1. Split daemon runtime from UI startup.
2. Add an event bus or queue abstraction for signals, logs, and service state.
3. Implement OKX WebSocket market/account streams instead of relying only on polling.
4. Expand FastAPI endpoints beyond runtime events into positions, orders, and strategy decisions.
5. Move JSON status files toward structured state storage, such as SQLite.
6. Add explicit strategy decision records: input data, BTC regime, signal, risk decision, action, and result.

## Near-Term Decision

Do not rewrite the whole UI first. Keep Textual while refactoring the backend boundary. Once the API/event layer is stable, build either:

- Textual plus FastAPI for local operator use, or
- FastAPI plus Vite/React or Next.js for a browser dashboard.

The current system is a workable MVP, but it should be refactored before depending on it as an always-on position management platform.
