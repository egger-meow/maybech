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

### Priority Reorder Justification - 2026-06-29

The operator explicitly canceled demo-account verification because a demo API
key is unavailable and directed the project toward staged real-account
preparation. Keeping the demo item first would permanently block the project
without reducing real-account danger. The production credential was also
disclosed in conversation, so using it before revocation would create a more
immediate loss risk than the superseded demo gate. This reorder is necessary,
not optional: real-money work must fail closed until the credential and account
mode are safe, then prove each execution lifecycle with minimum exposure.

1. Complete the staged real-account safety gate. Revoke the disclosed key and
   install a replacement production key with Trade permission only, withdrawals
   disabled, and an IP whitelist; prove authenticated `net_mode` read-only;
   keep strategies absent and entries disabled by default; then verify minimum-
   size open, cancellation, automatic close, protective-stop trigger, cleanup,
   and restart recovery one bounded stage at a time before normal operation.

## Non-Blocking / Later

Items here may be useful, but they must not interrupt `Current Priorities` while real-money safety blockers still exist.

Add work here only when it is not required to prevent uncontrolled behavior, excessive loss, incorrect live execution, broken operator control, unexpected state, or hidden safety threats.
