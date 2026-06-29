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

## External Blocked Gate

Credential rotation and real-account verification are externally blocked
because the operator cannot currently create another API key. The disclosed key
must not be used. Authenticated OKX calls, account verification, order-capable
actions, and live execution remain disabled. This gate blocks live verification
only; it does not block backend work using persisted state, typed interfaces,
mocks, and local tests.

## Current Priorities

### Priority Update Justification - 2026-06-29

The prior item combined an unavailable external credential action with all
backend readiness, causing unrelated backend work to stop. The operator
explicitly directed the project to keep the credential blocked while continuing
non-authenticated backend foundations. The next concrete operator-control and
state-correctness blocker is per-unit reduce execution, which must be correct
before real money can be managed from the product API.

1. Complete the execution-confirmed logical-position reduce lifecycle through
   typed backend interfaces and mocks. A confirmed reduce command must claim an
   exact unit quantity, safely cancel or resize its owned stop, submit only a
   reduce-only intent, wait for confirmed fills before changing quantity,
   restore exact protection for any remainder, recover unknown/canceled/restart
   outcomes idempotently, and persist complete audit evidence.

## Non-Blocking / Later

Items here may be useful, but they must not interrupt `Current Priorities` while real-money safety blockers still exist.

Add work here only when it is not required to prevent uncontrolled behavior, excessive loss, incorrect live execution, broken operator control, unexpected state, or hidden safety threats.
