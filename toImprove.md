# To Improve

Current blockers to dependable real-money operation and explicitly activated
major build phases belong here. An activated build phase remains a priority
until its written acceptance gates are complete; do not silently return it to
the backlog after a partial milestone.

This file is a priority contract, not a feature wishlist. Always sort items by real operational danger before adding or editing them. Once written, an earlier priority item is always higher than a later priority item. Always do the higher priority item first.

## Priority Rules

1. Add an item only if it is a concrete correctness, safety, or operator-control blocker, or the operator explicitly activates a major build phase with acceptance gates.
2. Do not add general cleanup, speculative features, refactors, or nice-to-have work.
3. If a new blocker is more dangerous than an existing item, explicitly reorder the list instead of appending it casually.
4. Keep priority items in strict order from most urgent to least urgent.
5. Remove an item when it is completed and verified. Do not automatically add a replacement item just because one was removed. The priority list does not need to stay the same length. After removal, close the gap, renumber the remaining items, and only add a new item if it independently qualifies as a necessary blocker under this document.
6. If an item is neither necessary nor an explicitly activated build phase, put it under `Non-Blocking / Later`, not under `Current Priorities`.

## Necessary Blocker Definition

An item is necessary only if leaving it unfixed could cause one or more of these:

1. Uncontrolled trading behavior.
2. Loss beyond the configured risk or stop settings.
3. Live-account behavior that differs from the operator's expectation.
4. Incorrect state after order placement, cancellation, partial fill, close, or restart recovery.
5. Missing or stale UI/runtime information that prevents fluent operator control.
6. A hidden or underlying safety threat that could become dangerous during real-money operation.

## Current Priorities

1. Complete the activated position-rule and market-analysis build phase.

   This is one end-to-end product milestone, not a collection of optional UI
   experiments. Work in the ordered phases below and commit each verified
   milestone so progress stays reviewable and recoverable.

   Build plan:

   1. Evidence and fixed-risk foundation — in progress:
      bounded Support/Resistance analysis, explicit degraded states, BTC-regime
      context, fixed-loss stop derivation, chart-anchored sizing, and a
      non-mutating operator calculator.
   2. Persisted rule design and guarded promotion:
      versioned typed rule definitions, structured evidence snapshots, and
      explicit reviewed promotion into strategy defaults or one logical
      position override. Research markers must remain non-executable until this
      transition succeeds.
   3. Initial protection and take-profit materialization:
      materialize percent/absolute/evidence stop and target rules from actual
      confirmed entry price; support fixed target, evidence target, staged
      reduce targets, and a remainder that can continue running.
   4. Break-even lifecycle:
      persisted arming thresholds, fee/slippage-adjusted target calculation,
      restart-safe state transitions, confirmed exchange protection amendment,
      and visible evidence explaining armed/applied/blocked state.
   5. Optional trailing lifecycle:
      activation threshold, distance, timeframe, high/low-water persistence,
      monotonic protection, restart recovery, stale-data fail-closed behavior,
      and separation between trailing stop and trailing take-profit semantics.
   6. Incremental market evidence:
      durable or bounded reusable candle state, incremental recalculation,
      cache/API/CPU limits, explicit invalidation, multi-evidence scoring, and
      deterministic stale/missing/duplicate/API-failure behavior.
   7. Integrated operator workflow:
      Strategy Management defaults, Position Management overrides, Market
      Analysis proposal selection, chart overlays for every active and executed
      level, unsaved/revision-conflict handling, and manual-review state when
      evidence conflicts or becomes stale.
   8. Completion verification:
      focused unit/integration/API/UI tests, generated-contract checks, full
      backend and frontend gates, restart simulations, and documentation that
      matches the actual shipped behavior.

   Acceptance gates — all are required before removing this priority:

   * strategy defaults and per-logical-position overrides use one typed,
     revision-protected rule model
   * fixed-loss and chart-anchored initial stops derive size without exceeding
     configured allowed loss after tick/lot rounding
   * take-profit supports fixed percent, fixed price, evidence target, staged
     reduction, and an optional running remainder
   * break-even is fee/slippage adjusted, persisted, restart-safe, and changes
     live protection only after confirmed exchange amendment
   * trailing protection is optional, monotonic, persisted, bounded, and fails
     closed on stale or missing market data
   * Support/Resistance evidence is cached, incrementally calculated, bounded,
     freshness-aware, invalidated explicitly, and never directly executable
   * the UI visibly distinguishes fresh, partial, stale, unavailable, proposed,
     armed, applied, invalidated, and manual-review states
   * entry/current/stop/target/break-even/trailing/reduce/close overlays come
     from typed API data and confirmed execution evidence
   * stale revisions cannot overwrite newer strategy or position rules
   * tests cover stale/missing/duplicate candles, API failure, restart,
     partial fills, staged exits, break-even, trailing monotonicity, and rule
     promotion boundaries
   * full backend tests, frontend contract/lint/typecheck/build, and a
     requirement-by-requirement completion audit pass

   Detailed product direction and current progress follow.

  Phase progress: the backend now has a bounded, short-lived cached
  `GET /market/analysis/support-resistance` research endpoint. It reports
  freshness, missing/duplicate/invalid candle quality, clustered extrema, and
  volume/wick/recency/ATR/invalidation-distance evidence, and is contractually
  marked ineligible as a live rule. The responsive Market Analysis page now
  exposes K-lines, level markers, evidence scores, and explicit
  fresh/partial/unavailable state. Fixed-loss stop derivation and
  chart-anchored position sizing are available as non-mutating, lot-aligned
  proposals with structured stop expressions and evidence. BTC regime now
  contributes a visible, bounded confidence adjustment without becoming an
  authority. True incremental recalculation and reviewed rule promotion
  into strategy defaults or position overrides remain to be built.

  The goal is to design a coherent rule system for stop-loss, take-profit, break-even, optional trailing protection, and research-grade Support/Resistance evidence after a strategy opens a position or when the operator edits an existing logical position unit.

  This work should eventually support both strategy default rules and per-logical-position overrides:

  * initial stop-loss rules
  * take-profit / stop-win rules
  * break-even arming rules
  * optional trailing stop / trailing take-profit rules
  * manual-review rules when evidence conflicts or data is stale
  * chart overlays for entry, current price, stop-loss, take-profit, break-even, and executed reduce/close markers
    Stop-loss design should support at least two modes:

  1. Fixed-loss stop mode:
     the operator chooses a fixed acceptable loss amount for the unit, and the system derives or displays the stop level / distance clearly. This is fast and simple, but may ignore market structure.

  2. Chart-anchored stop mode:
     the operator chooses or the system proposes a stop near market structure, such as prior high / prior low, 2B invalidation level, recent swing level, or Support/Resistance level across an explicit timeframe. The position size should then be calculated from the fixed allowed loss at that stop level.

     Example principle:
     `position_value = allowed_loss / (abs(entry_price - stop_price) / entry_price)`

     If BTC is 65,000, the selected long stop is 60,800, and allowed loss is 2,000 USDT, the system should derive the max position value from that distance instead of letting arbitrary size create arbitrary loss.
     Break-even design should be first-class. After sufficient favorable movement, such as real PnL reaching a configured percent or price reaching a configured evidence level, the system may arm or move the stop-loss to a fee/slippage-adjusted break-even level. Break-even should protect the position so expected realized PnL is at least zero or slightly positive after fees and slippage, not blindly equal to raw entry price.

  Take-profit / stop-win design should support multiple future styles:

  * fixed percent from actual entry price
  * fixed price level after entry is known
  * chart/evidence target, such as next Support/Resistance, prior high/low, or operator-selected level
  * staged reduce targets for partial exits
  * trend-following mode where only part of the position is reduced and the rest is allowed to run
    Trailing stop / trailing take-profit is useful but lower priority than initial stop-loss and break-even. It must be optional because aggressive trailing can close trend trades too early during normal volatility. If implemented, it should expose distance, activation threshold, timeframe, and evidence clearly.

  (New page for market analysis, and data also related to above) Support/Resistance analysis should be treated as an evidence provider, not an authority. It may revisit peak/valley detection and draw circles, markers, and level values over K-lines, but it must combine extrema with broader technical market evidence such as timeframe, volume, recency, repeated touches, wick/body behavior, BTC regime, volatility, and invalidation distance. One extrema algorithm must not directly control trading.

  Any market-data analyzer built for this must have:

  * cached market fetches
  * incremental calculations
  * explicit freshness timestamps
  * bounded CPU and API usage
  * invalidation rules
  * test coverage for stale data, missing candles, duplicated candles, and API failure
  * visible UI state when evidence is stale, partial, or unavailable
    Price logic used for actual trading must enter the persisted strategy / logical-position expression model with structured evidence. Research-only chart markers must not silently become live close rules.

  The prerequisite strategy-management and logical-position mutation APIs are
  now revision-protected and tested. This phase is therefore active and stays
  in `Current Priorities` until every acceptance gate above is verified.

## Non-Blocking / Later

Items here may be useful, but they must not interrupt `Current Priorities`.

Add work here only when it is outside the activated phase and is not required
to prevent uncontrolled behavior, excessive loss, incorrect live execution,
broken operator control, unexpected state, or hidden safety threats.

- Frontend polish, broad architecture cleanup, and unrelated refactors remain
  deferred until the backend execution finish line is proven.
- The execution-leader/read-replica boundary now exists, and replica SQLite
  connections are database-enforced read-only. Keep SQLite replicas same-host;
  PostgreSQL plus distributed leader routing is still required before
  multi-host or mutating replicas can share one account.
- Backtesting is documented as a future Strategy Management capability, but no
  current backtest engine exists in `src/`; do not expose a fake API or block
  live position management on rebuilding that research subsystem.
- Notification delivery is intentionally basic: configured LINE/Gmail sends,
  bounded retry, backlog, and last health are sufficient for now. Canary
  probes, full attempt history, and manual-test health isolation are deferred
  unless notification reliability becomes an explicit product priority.
