# To Improve

This file tracks the main improvement points agents should keep visible while
working on Maybech. Maintain at least three active items at all times.

## Current Top Priorities

1. Implement confirmed live close-order execution in `PositionManagerService`
   before any live trade is marked closed in `TradeStore`.
2. Harden frontend/backend API contracts with typed response models, validation,
   and build checks so dashboard cards cannot drift from FastAPI payloads.
3. Protect trade and rule ownership boundaries with regression tests for attach,
   delete, close, and cross-trade mutation paths.

## Maintenance Rules

- Read this file before making code or docs changes.
- Update the list whenever an item is completed, replaced, or made obsolete.
- Keep each item actionable and tied to concrete files, commands, or behavior.
- Preserve at least three current priorities so the next contributor has a clear
  direction.
