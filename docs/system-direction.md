# Maybech System Direction

## Product Goal

Maybech should be a robust auto-signaling and action-trigger system for perpetual position management. It is not primarily a high-frequency or fully autonomous quant trading bot. The core loop should detect market state, produce explainable signals, manage position actions safely, and present a precise, vivid, interactive control surface.

The product is centered on two first-class workflows:

- Strategy management: pre-position plans that watch composable signals and
  create new logical position units after risk checks.
- Position management: post-entry control of each logical position unit with
  independent stop-loss, take-profit, break-even, reduce, and close rules.

OKX can merge repeated entries on the same swap side into one exchange position.
Maybech must not model that merged OKX row as the only position. Every open or
add action should become a separate logical position-management unit, optionally
tagged with the creating strategy, so rules can be managed independently.

## Current Fit

The current codebase is a good Python MVP:

- `src/daemon/` already separates background services from the UI.
- `StrategyService` and `NotificatorService` can run continuously.
- `frontend/` provides the browser dashboard for control and inspection.
- OKX order placement has safety guards through dry-run and explicit arming.

The main limitation is remaining process coupling in the daemon runtime. The API-backed runner now exposes service state directly, but longer-running production behavior still needs stronger persistence and authentication.

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
                         Next.js Web Frontend
```

The first Python API boundary now exists through `src/runtime/`,
`run_api.py`, and `src/api/app.py`.
It exposes:

- service status and enable/disable controls
- recent runtime events
- a WebSocket stream for live frontend updates
- generated signal and notification events published by daemon services
- account summary, open positions, and recent order snapshots
- current BTC-led market regime
- strategy action decisions with allow/block reasons
- position-management intents for existing perpetual positions

Durable strategy decision records now include signal reasons, BTC policy
evidence, submission results, and correlated trade/position references. The
confirmed-fill allocation boundary now handles idempotent partial fills and
weighted entry prices. Authenticated REST polling now provides restart-safe OKX
fill catch-up. The next expansion is private websocket cancellation/latency
handling plus editable operator review states for manual position management.

See `docs/project-charter.md`, `docs/domain-model.md`, `docs/api-spec.md`, and
`docs/ui-direction.md` for the canonical product concepts and target API/UI
shape.

The web UI now lives in `frontend/` using Next.js.

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
the BTC direction, strength, impulse, setup prices, allow/block reason, and a
correlation id. The API exposes both the latest runtime snapshot at
`GET /strategy/decisions` and durable history at
`GET /strategies/{strategy_id}/decisions`.

Open positions also flow through `PositionIntentService`, which emits
`position.intents` snapshots from the latest account state and BTC regime. That
gives the frontend a direct feed for hold/reduce/close/manual-review guidance.
`PositionManagerService` evaluates persisted logical-position close conditions
against runtime and candle-derived signal context for dry-run close simulation,
and armed live triggers automatically submit reduce-only close orders. Units
remain `closing` until authenticated OKX fills confirm partial or complete exit.

## Always-On Deployment

For a personal always-running PC setup, two stages make sense:

1. Short term: run the Python daemon at OS startup with Windows Task Scheduler.
2. Medium term: run backend services in Docker Compose.

Docker is recommended once the API layer exists. Use separate containers for backend, frontend, and optional storage. Do not expose OKX-control endpoints publicly through port forwarding without authentication, TLS, and strict allowlists. Prefer local-only access, VPN/Tailscale, or reverse proxy authentication.

This repo now includes a conservative `Dockerfile` and `docker-compose.yml` for the API runtime. The compose file binds the API to localhost only. See `docs/deployment.md` for the operational notes.

## Refactor Priorities

1. Keep runtime startup centralized in `src/runtime/` and keep `run_api.py` and
   `run_services.py` as thin compatibility wrappers.
2. Add an event bus or queue abstraction for signals, logs, and service state.
3. Implement OKX WebSocket market/account/order streams while retaining REST
   polling for reconnect catch-up.
4. Expand FastAPI endpoints beyond runtime events into strategies, logical
   position units, exchange positions, orders, signal evaluation, and strategy
   decisions.
5. Move JSON/in-memory runtime state toward structured storage, likely SQLite,
   with explicit retention rules.
6. Add explicit strategy decision records: input data, BTC regime, signal,
   risk decision, action, and result.
7. Add a reconciliation layer between Maybech logical position units and OKX net
   positions.

## Near-Term Decision

Keep improving the API/event layer and the Next.js dashboard rather than maintaining a parallel terminal UI.

The current system is a workable MVP, but it should be refactored before depending on it as an always-on position management platform.
