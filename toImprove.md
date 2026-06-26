# To Improve

This file tracks the main improvement points agents should keep visible while
working on Maybech. Maintain at least three active items at all times.

## Current Top Priorities

1. Implement confirmed live close-order execution in `PositionManagerService`
   before any live trade is marked closed in `TradeStore`.
2. Implement execution-confirmed allocation for partial fills, merged OKX rows,
   fees, and close/reduce quantities so each logical unit's remaining quantity
   stays authoritative.
3. Add signal-expression validation/evaluation and require validation before
   enabling a strategy for runtime execution.
4. Add authentication and operator authorization before exposing any service
   control or trading-control endpoint beyond localhost.
5. Move runtime state that must survive restarts from in-memory snapshots toward
   structured persistence with explicit retention rules.
6. Define persisted audit event and decision-history schemas using the shared
   SQLite schema helper and explicit migration steps.
7. Consolidate dependency metadata so `requirements.txt`, `pyproject.toml`, and
   `uv.lock` no longer describe different dependency sources of truth.

## Maintenance Rules

- Read this file before making code or docs changes.
- Update the list whenever an item is completed, replaced, or made obsolete.
- Keep each item actionable and tied to concrete files, commands, or behavior.
- Preserve at least three current priorities so the next contributor has a clear
  direction.
