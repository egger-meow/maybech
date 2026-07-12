# Maybech System Direction

## Runtime Boundary

Runtime policy is modeled by `RuntimeMode`, not inferred from a dry-run boolean.
Exchange connectivity and order permission are independent capabilities. Demo
and Live Armed use disjoint credential namespaces/endpoints; Live Safe connects
to production only for inspection and recovery and never arms mutation;
Simulation remains the default.

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
- Strategy, account, reconciliation, position, and fill services can run continuously.
- `frontend/` provides the browser dashboard for control and inspection.
- OKX order placement has safety guards through explicit runtime modes and arming.

The main limitation is remaining process coupling in the daemon runtime. The API-backed runner now exposes service state directly, but longer-running production behavior still needs stronger persistence and authentication.

Runtime roles now make that coupling explicit: one `combined` process owns
execution and live state, while a `replica` process is read-only and starts no
daemon. This permits bounded read scaling and provides a migration boundary,
but multi-host scaling still requires shared transactional storage and routing
mutations/live streams to the leader.

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
weighted entry prices. Authenticated private order events reduce latency, while
REST polling provides restart-safe catch-up after every reconnect. The next
expansion is explicit operator controls and recovery for external positions.

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
`GET /market/btc-regime`. Strategy and position action policies consume this
state explicitly before execution.

Strategy execution now evaluates persisted composable signal expressions and
uses `BTCRegimeActionPolicy` before placing an entry. Each false-to-true signal
edge becomes a structured `strategy.action_decision` event with the BTC
direction, strength, impulse, entry price, allow/block reason, evidence, and a
correlation id. SQLite match state prevents repeated entries while a condition
remains true across ticks or restarts. The API exposes both the latest runtime snapshot at
`GET /strategy/decisions` and durable history at
`GET /strategies/{strategy_id}/decisions`.

Open positions also flow through `PositionIntentService`, which emits
`position.intents` snapshots from the latest account state and BTC regime. That
gives the frontend a direct feed for hold/reduce/close/manual-review guidance.
`PositionManagerService` evaluates persisted logical-position close conditions
against runtime and candle-derived signal context for Simulation close handling,
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
3. Extend the built private order stream only when another live workflow needs
   account, position, or market channels; retain REST for reconnect catch-up.
4. Expand FastAPI endpoints beyond runtime events into strategies, logical
   position units, exchange positions, orders, signal evaluation, and strategy
   decisions.
5. Move JSON/in-memory runtime state toward structured storage, likely SQLite,
   with explicit retention rules.
6. Add explicit strategy decision records: input data, BTC regime, signal,
   risk decision, action, and result.
7. Add a reconciliation layer between Maybech logical position units and OKX net
   positions.

## Market Intelligence Layer

A separate initiative (`docs/market-intelligence.md`) evolves the existing
`GET /market/macro-overview` (`src/market/macro_overview.py`) from an
in-memory-cached macro snapshot into a persisted, provider-agnostic market
regime layer upstream of the signal engine. It extends the existing macro
provider functions rather than replacing them, and does not change the
runtime-mode, order-placement, or storage-authority boundaries described
above.

## Near-Term Decision

Keep improving the API/event layer and the Next.js dashboard rather than maintaining a parallel terminal UI.

The current system is a workable MVP, but it should be refactored before depending on it as an always-on position management platform.
