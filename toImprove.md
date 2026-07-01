# To Improve

Only current blockers to dependable real-money operation belong here.

This file is a priority contract, not a feature wishlist. Always sort items by real operational danger before adding or editing them. Once written, an earlier priority item is always higher than a later priority item. Always do the higher priority item first.

## Priority Rules

1. Add an item only if it is a concrete correctness, safety, or operator-control blocker.
2. Do not add general cleanup, speculative features, refactors, or nice-to-have work.
3. If a new blocker is more dangerous than an existing item, explicitly reorder the list instead of appending it casually.
4. Keep priority items in strict order from most urgent to least urgent.
5. Remove an item when it is completed and verified. Do not automatically add a replacement item just because one was removed. The priority list does not need to stay the same length. After removal, close the gap, renumber the remaining items, and only add a new item if it independently qualifies as a necessary blocker under this document.
6. If an item is not necessary, put it under `Non-Blocking / Later`, not under `Current Priorities`.

## Necessary Blocker Definition

An item is necessary only if leaving it unfixed could cause one or more of these:

1. Uncontrolled trading behavior.
2. Loss beyond the configured risk or stop settings.
3. Live-account behavior that differs from the operator's expectation.
4. Incorrect state after order placement, cancellation, partial fill, close, or restart recovery.
5. Missing or stale UI/runtime information that prevents fluent operator control.
6. A hidden or underlying safety threat that could become dangerous during real-money operation.

## Current Priorities

1. Instrument metadata has an explicit refresh API but no automatic daily
   refresh or stale-cache indicator. Long-running operators can otherwise size
   against outdated lot(since even new added inst we will mostly not trade, so even outdated like a week is okay), minimum, tick, or contract-value data.
2. Automatically recovered OKX units correctly block entries and request manual
   review, but Position Management has no guided adoption flow to add a
   side-correct stop and prove owned protection before clearing that review.
3. Generic LINE cooldown is implemented and legacy price alerts are gone, but
   lifecycle audit categories are not yet routed to LINE/Gmail. Phone/email
   monitoring therefore does not yet cover strategy, position, runtime-safety,
   and exchange/API failures end to end.

## Non-Blocking / Later

Items here may be useful, but they must not interrupt `Current Priorities` while real-money safety blockers still exist.

Add work here only when it is not required to prevent uncontrolled behavior, excessive loss, incorrect live execution, broken operator control, unexpected state, or hidden safety threats.

- Future Support／Resistance Analysis page: this is an operator research view,
  not a standalone alert service. It may revisit peak/valley detection and draw
  circles/markers plus level values over K-lines, but should combine them with
  broader technical market evidence rather than treating one extrema algorithm
  as authoritative. Design cached market fetches, incremental calculations,
  explicit freshness, bounded CPU/API usage, and invalidation before rebuilding
  any analyzer. Price logic used for trading must still enter the persisted
  strategy/position expression model with evidence.

- Frontend polish, broad architecture cleanup, and unrelated refactors remain
  deferred until the backend execution finish line is proven.
- The execution-leader/read-replica boundary now exists, and replica SQLite
  connections are database-enforced read-only. Keep SQLite replicas same-host;
  PostgreSQL plus distributed leader routing is still required before
  multi-host or mutating replicas can share one account.
- Backtesting is documented as a future Strategy Management capability, but no
  current backtest engine exists in `src/`; do not expose a fake API or block
  live position management on rebuilding that research subsystem.
