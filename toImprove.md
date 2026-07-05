# To Improve

Current blockers to dependable real-money operation and explicitly activated
major build phases belong here. An activated build phase remains a priority
until its written acceptance gates are complete; do not silently return it to
the backlog after a partial milestone.

This file is a priority contract, not a feature wishlist. Always sort items by
real operational danger before adding or editing them. Once written, an earlier
priority item is always higher than a later priority item.

## Priority Rules

1. Add an item only if it is a concrete correctness, safety, or
   operator-control blocker, or the operator explicitly activates a major build
   phase with acceptance gates.
2. Do not add general cleanup, speculative features, refactors, or nice-to-have
   work.
3. If a new blocker is more dangerous than an existing item, explicitly
   reorder the list instead of appending it casually.
4. Keep priority items in strict order from most urgent to least urgent.
5. Remove an item when it is completed and verified. Do not automatically add
   a replacement item. Close the gap, renumber the remaining items, and only
   add new work if it independently qualifies under this document.
6. If an item is neither necessary nor an explicitly activated build phase,
   put it under `Non-Blocking / Later`, not under `Current Priorities`.
7. Treat every checklist as a shrinking queue. Once a step or acceptance gate
   is verified, remove it instead of appending a progress narrative. Do not
   replace removed work with speculative follow-up work.
8. Keep historical implementation evidence in Git commits and canonical docs.
   This file is not a changelog.

## Necessary Blocker Definition

An item is necessary only if leaving it unfixed could cause one or more of:

1. Uncontrolled trading behavior.
2. Loss beyond configured risk or stop settings.
3. Live-account behavior that differs from operator expectation.
4. Incorrect state after placement, cancellation, partial fill, close, or
   restart recovery.
5. Missing or stale UI/runtime information that prevents fluent control.
6. A hidden safety threat that could become dangerous in real-money operation.

## Current Priorities

1. Make trade history distinguish separate logical executions. The Simulation
   audit displayed five visually identical stop-loss rows and five visually
   identical take-profit rows, while `/trades/history` proved they have unique
   trade IDs, correlation IDs, and entry times roughly 10–12 seconds apart.
   Expose a stable short identity plus entry/exit timing or expandable audit
   evidence so operators can distinguish repeated signal edges from duplicate
   ingestion; verify repeated same-strategy exits remain individually traceable
   on desktop and mobile before removing this item.

2. Make the documented local frontend URL work cleanly in development. A real
   Next.js 16.2.9 run opened at `http://127.0.0.1:3000` blocks
   `/_next/webpack-hmr` as a cross-origin development resource because
   `frontend/next.config.ts` does not include `127.0.0.1` in
   `allowedDevOrigins`, even though the backend and testing instructions use
   that host. Add the narrow development origin, verify HMR/reconnect from both
   documented loopback hostnames, and retain Next.js's default protection for
   unlisted origins before removing this item.

The completed position-rule and market-analysis phase is audited in
`docs/position-rule-phase-audit.md`.

## Non-Blocking / Later

Items here may be useful, but they must not interrupt `Current Priorities`.

Add work here only when it is outside an activated phase and is not required to
prevent uncontrolled behavior, excessive loss, incorrect live execution,
broken operator control, unexpected state, or hidden safety threats.
- Replace the current custom candle UI in Position Management with a real chart engine.
  Use KLineChart as the first candidate because Maybech needs interactive financial K-lines with overlays, labels, markers, and future drawing/editing support. Do not build a custom candle renderer.
  Implement:
   1. GET /market/candles?inst_id=...&bar=...&limit=...
   2. GET /positions/logical/{position_id}/chart returning candles plus overlays:
      entry, current_price, stop_loss, take_profit, break_even, and event markers.
   3. Frontend reusable PositionKlineChart component.
   4. Render it inside each logical position unit card.
   5. Keep it read-only first. Do not add drag-to-edit until the save/confirmation path is explicit.
   6. Use generated OpenAPI types and add contract tests.
  Stay inside current product direction: improve Position Management visualization only. No new architecture, no unrelated cleanup.
- Frontend polish, broad architecture cleanup, and unrelated refactors remain
  deferred until independently prioritized.
- The execution-leader/read-replica boundary exists and replica SQLite
  connections are database-enforced read-only. Keep SQLite replicas same-host;
  PostgreSQL plus distributed leader routing is still required before
  multi-host or mutating replicas can share one account.
- Backtesting is a future Strategy Management capability, but no current engine
  exists in `src/`; do not expose a fake API.
- Notification delivery intentionally remains basic: configured LINE/Gmail
  sends, bounded retry, backlog, and last health are sufficient unless
  notification reliability becomes an explicit priority.
