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

1. Add the missing frontend Entry Enable/Kill control. The armed Demo audit
   proved every page only reads `/risk/entries`; no component calls the guarded
   enable/kill endpoints, even though the UI says entries must be explicitly
   enabled and tells operators to use a Kill Switch before risk edits. Provide
   an unmistakable, confirmation-gated enable action and an always-reachable
   kill action that reports pending-order cancellation results and partial
   failures without affecting reduce-only exits; verify rapid clicks,
   in-flight submission serialization, restart-disabled state, and degraded API
   behavior before removing this item.

2. Preserve in-progress Position rule edits across normal live polling. In the
   armed Demo test, changing the `0.02`-contract unit's stop from `60000` to the
   reviewed `61215.9` correctly displayed `-0.3053 USDT`, but the 5-second SWR
   refresh detached/remounted the editor, restored `60000`, and disabled the
   confirm button before it could be clicked. An immediate retry reached the
   API but normal background reconciliation/materialization had advanced the
   position revision, producing `409 "logical position changed since stop
   review"`; the UI hid that conflict after refresh. Stop touching revisions
   for evidence-equivalent observations, keep drafts stable while merging newer
   server state explicitly, surface actionable conflicts, and verify stop,
   take-profit, staged exit, break-even, trailing, and reduce drafts under
   repeated polling before removing this item.

3. Guard edits to enabled strategies in order-capable runtimes. With Demo armed
   and entries enabled, Playwright changed the active entry threshold from `0`
   to `100000` and the frontend persisted it immediately without confirmation,
   forced disable/review, or a visible pending version; the same editor owns
   sizing and default stop/take-profit rules. Require an explicit reviewed
   transition that cannot race signal evaluation or entry submission, preserve
   the last executable version until approval, and verify entry, sizing,
   stop-loss, take-profit, break-even, trailing, stale-revision, and rapid-save
   edits under active ticks before removing this item.

4. Do not mark confirmed Maybech entries as unexplained external exposure. The
   first Playwright Demo strategy order filled, received an active quantity-
   matched OKX stop, and reconciled `balanced`, yet its own pending-open overlap
   permanently set `requires_manual_review=true`; Position Management displayed
   `需人工對帳` and an external-increase warning. The same record left its
   top-level client/exchange order IDs blank even though confirmed IDs existed
   in metadata and allocations. Make pending-open/fill/account ordering
   converge without false review, populate canonical identifiers atomically,
   and interpret OKX net-mode positions by signed quantity rather than exposing
   `posSide=net` as `side=unknown` with a false manual-review intent. Verify
   both event orderings, WebSocket/REST races, restart, repeated
   entries, and genuinely external increases before removing this item.

5. Restore authenticated dashboard operation. A real Playwright Simulation run
   on 2026-07-04 proved that a configured `MAYBECH_API_TOKEN` makes every
   protected dashboard request return `401`: `frontend/lib/api.ts` defines
   `configureApiToken()`, but no frontend code calls it and the UI provides no
   operator authentication flow. The dashboard consequently reports an unknown
   execution mode and leaves account/market data loading despite a healthy API.
   Provide a secure local operator token flow that covers HTTP and WebSocket
   clients without rendering, logging, or persisting the token insecurely; show
   an explicit authentication-required state instead of generic loading/error
   placeholders; verify all dashboard routes and reconnect behavior against an
   authenticated Simulation runtime before removing this item.

6. Isolate malformed OKX instrument rows instead of aborting every Demo/Live
   startup. The 2026-07-04 Demo launch reached OKX successfully, but one
   `TESTING-USDT-SWAP` payload omitted `lotSz`, `minSz`, or `tickSz`;
   `InstrumentMetadataStore.replace_type()` raised on that unrelated row and
   killed the required Account service before the API bound. Preserve strict
   validation and explicit evidence for rejected rows, but allow a valid,
   tradable allowlisted catalog to refresh atomically without the malformed
   instrument; prove preflight still fails when a required/active instrument is
   invalid and that Demo plus Live Armed start when only an unrelated row is
   bad before removing this item.

7. Expose continuous execution correctness health in every order-capable
   dashboard. Demo preflight passed and the backend proved REST fill catch-up
   current plus the private order WebSocket connected, but the runtime banner
   never requests `/execution/fills/status`; after startup it cannot disclose a
   stale cursor, cursor errors, stream disconnect/reconnect/drop state,
   allocation conflicts, missing-fill alerts, or protection errors. Add
   freshness-aware fail-closed diagnostics without implying that a historical
   preflight result is current, and verify transitions during disconnect,
   reconnect, catch-up, and error recovery before removing this item.

8. Quarantine and deduplicate rejected fill evidence and notifications. After
   the Demo forced-TP test left one invalid historical rule, fill catch-up
   repeatedly emitted the same `execution.fill_rejected` for bill
   `3711878553714458626` and LINE delivered the unchanged “take_profit price
   must be above entry for long” alert roughly every four minutes for hours.
   Persist one terminal/quarantined disposition per immutable bill and error
   signature, allow the correctness cursor to progress without forgetting the
   defect, notify once plus meaningful state transitions only, and verify
   retry/backoff/restart behavior does not resend identical alerts before
   removing this item.

9. Present account-mode-correct available collateral and unrealized PnL. The
   armed Demo audit returned populated EUR/USDT per-currency `availBal` and
   `upl`, but top-level OKX `availEq` was blank and
   `Dashboard.get_account_summary()` never creates `unrealized_pnl`; the home
   page consequently showed `資料不足` for both fields while hiding the usable
   currency breakdown. Define correct semantics for supported OKX account
   levels without summing unlike currencies incorrectly, expose currency and
   valuation evidence, and verify zero, nonzero, multi-currency, and unavailable
   states against authenticated snapshots before removing this item.

10. Make a fresh Simulation workspace operable without violating exchange
   isolation. A real Playwright run on a database with no instrument cache
   proved that Strategy, Position, and Risk pages all block their core inputs;
   Strategy offers `立即更新商品資料`, but `POST /instruments/refresh` correctly
   returns `409 "Simulation does not connect to OKX"` and the UI surfaces no
   failure. Define an explicit safe instrument-metadata bootstrap/import path
   for Simulation (without silently contacting OKX), stop offering an action
   that can only fail, expose refresh/import errors to the operator, and verify
   that a fresh isolated Simulation database can configure risk, author a
   strategy, and create a logical position before removing this item.

11. Provide one consistent searchable SWAP selector across Strategy creation,
   Position creation, Market Analysis, and risk allowlists. With no prior
   selection, open a real dropdown containing at least three to five useful
   liquid/hot SWAP candidates; as the operator types, filter and show matching
   cached SWAPs immediately instead of an empty dropdown. Do not hard-limit the
   discovery UI to the current risk allowlist: show boundary eligibility and
   enforce it at save/enable/preflight, while filtering out non-SWAP products.
   Use an explicit safe bootstrap when no cache exists and verify keyboard,
   mouse, empty-query, no-result, stale-cache, low-price, desktop, and mobile
   behavior before removing this item.

12. Prevent Next.js 16.2.9 Turbopack from terminating the dashboard development
   server while processing Traditional Chinese source. During the real
   Playwright run, `next-code-frame/src/highlight.rs` panicked because a byte
   index landed inside the UTF-8 character `組`; the next launch reported an
   internal Turbopack error and deleted its filesystem cache. Reproduce with a
   clean cache, identify whether source-map/code-frame input or the pinned Next
   release is responsible, apply the narrowest supported mitigation, and prove
   repeated navigation/edit/HMR cycles across Chinese-heavy pages do not stop
   the frontend before removing this item.

13. Remove the persisted-theme hydration mismatch. The Playwright interaction
   pass proved that switching to light mode persists correctly, but a reload
   makes `ThemeToggle` server-render a sun icon while its client initializer
   reads local storage and renders a moon icon; React reports hydration failure
   and regenerates the subtree. Use a hydration-safe theme initialization that
   preserves the operator choice without a misleading flash, then verify clean
   console output on first load, reload, and navigation in both themes before
   removing this item.

14. Localize all operator-facing protection lifecycle controls consistently in
   Traditional Chinese(some english ok but mainly chinese). Position Management currently mixes the Chinese shell
   with English sections and statuses such as `Automatic cost-adjusted
   break-even`, `Activation profit`, `Persisted target stop`, `applied`, and
   the optional trailing lifecycle. Translate labels, state, validation,
   confirmations, and failure/recovery text while retaining exact exchange
   identifiers where necessary; verify desktop/mobile layouts and every
   inactive, pending, applied, failed, and restart-restored state before
   removing this item.

15. Make trade history distinguish separate logical executions. The Simulation
   audit displayed five visually identical stop-loss rows and five visually
   identical take-profit rows, while `/trades/history` proved they have unique
   trade IDs, correlation IDs, and entry times roughly 10–12 seconds apart.
   Expose a stable short identity plus entry/exit timing or expandable audit
   evidence so operators can distinguish repeated signal edges from duplicate
   ingestion; verify repeated same-strategy exits remain individually traceable
   on desktop and mobile before removing this item.

16. Make the documented local frontend URL work cleanly in development. A real
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
