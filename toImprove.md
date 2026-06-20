# To Improve

This file tracks the main improvement points agents should keep visible while
working on Maybech. Maintain at least three active items at all times.

## Current Top Priorities

1. Implement confirmed live close-order execution in `PositionManagerService`
   before any live trade is marked closed in `TradeStore`.
2. Harden frontend/backend API contracts with typed response models, validation,
   generated client types, and build checks so dashboard pages cannot drift from
   FastAPI payloads.
3. Add authentication and operator authorization before exposing any service
   control or trading-control endpoint beyond localhost.
4. Move runtime state that must survive restarts from in-memory snapshots toward
   structured persistence with explicit retention rules.

## Maintenance Rules

- Read this file before making code or docs changes.
- Update the list whenever an item is completed, replaced, or made obsolete.
- Keep each item actionable and tied to concrete files, commands, or behavior.
- Preserve at least three current priorities so the next contributor has a clear
  direction.
