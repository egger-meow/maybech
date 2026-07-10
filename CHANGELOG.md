# Changelog

All notable release entries for Maybech are recorded here.

## v0.1.0 - 2026-07-11

Initial GitHub release baseline for the local-first OKX perpetual trading
workspace.

### Included

- Python daemon runtime with Simulation, Demo, Live Safe, and Live Armed modes.
- FastAPI and WebSocket control surface for runtime state, account snapshots,
  strategies, risk limits, logical positions, audit events, and notifications.
- Next.js dashboard for strategy management, position management, market
  analysis, notification health, and live-readiness diagnostics.
- Persisted signal-based strategies, typed position rules, logical position
  units, fill allocation, owned protective stops, and SQLite-backed audit data.
- Guarded demo and production verification path documented in deployment notes.

### Safety Baseline

- Simulation remains the default runtime mode.
- Live order submission requires explicit `--mode live_armed`,
  `MAYBECH_ARM_ORDERS=1`, valid preflight, enabled account risk limits,
  reviewed strategies, and a separately confirmed entry gate.
- Reduce-only exits update logical quantities only after authenticated exchange
  fill confirmation.
- The API binds to loopback by default; non-loopback access requires explicit
  opt-in and bearer authentication.

### Known Limits

- This is a v0 operator-assist release, not a promise of unattended real-money
  profitability or loss bounds.
- SQLite is the supported local-first store. Same-host read replicas are
  bounded; multi-host deployment still requires shared transactional storage and
  mutation routing to one execution leader.
- General runtime event retention remains partial; durable audit coverage
  focuses on trading lifecycle and operator-control events.
