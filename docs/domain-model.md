# Domain Model

This file names the core Maybech concepts so future code, UI, and API work use
the same language.

## Signal

A signal is a structured market condition. It evaluates market/account/runtime
inputs and returns:

- `matched`: whether the condition is currently true
- `confidence` or strength when available
- `evidence`: prices, candles, levels, time windows, and comparison values
- `evaluated_at`: timestamp of evaluation

Signals can be primitive or composite.

Primitive examples:

- `price_above(symbol, level)`
- `price_below(symbol, level)`
- `rapid_drop(symbol, window_seconds, percent_or_points)`
- `rapid_rise(symbol, window_seconds, percent_or_points)`
- `volume_multiple(symbol, timeframe, lookback, multiplier)`

Composite examples:

- `btc_rapid_drop AND eth_price_below_level`
- `btc_price_above_level OR market_impulse_up`
- `(volume_spike AND price_gap) AND NOT risk_blocked`

## Strategy

A strategy is a pre-position plan. It owns entry conditions and default position
management rules. A strategy should not be treated as an already-open position.

Minimum strategy fields:

- stable strategy id and display name
- target instrument, side, and size policy
- entry signal expression
- risk limits and BTC regime filters
- default stop-loss, take-profit, trailing, and break-even rules
- enabled/disabled state
- backtest and live-readiness status

## Logical Position Unit

A logical position unit is Maybech's independent management object for a single
open/add action. It may map to part of an OKX net position rather than a separate
OKX position row.

Minimum logical position fields:

- stable logical position id
- OKX instrument id and side
- opened quantity and remaining quantity
- entry price for this unit
- source: strategy id, manual action, import, or recovery
- close conditions: persisted signal expressions for stop-loss, take-profit,
  break-even, trailing, manual review, or generic exit handling
- current status: planned, pending_open, open, reducing, closing, closed, failed
- audit events and exchange order references
- one owned exchange-protection record with the OKX algo id, algo client id,
  protected quantity, stop level, lifecycle state, and triggered child order

The first persistent implementation lives in `src/trading/logical_position_store.py`.
It stores logical units in SQLite and can backfill compatibility records from
existing `TradeStore` trades. That is a bridge toward the final lifecycle model,
not the complete reconciliation/execution layer.
The same store owns `logical_position_close_conditions`, which keeps close
management attached to the independent logical unit instead of the merged OKX
net position or legacy trade row.

`src/trading/position_reconciliation.py` compares active logical units with OKX
net-position snapshots and reports whether the group is balanced,
under-allocated, over-allocated, missing an exchange position, or missing enough
quantity data. It does not silently decide which logical unit was reduced when
OKX only exposes aggregate state.

## OKX Net Position

An OKX net position is the exchange's current aggregate representation. In net
or merged behavior, repeated entries on the same instrument and side can be
shown as one position with combined size and averaged entry price.

Maybech must not use the OKX net position as the only position-management unit.
Instead, it should reconcile many logical position units against one exchange
position when needed.

API responses may include matching OKX net-position snapshot data for context,
but rule ownership and lifecycle state belong to logical position units.

## Position Group

A position group is an optional view over several logical position units that
share an instrument, side, strategy, or OKX net position. It is useful for UI
summary and risk inspection, but close rules still belong to logical units.

## Entry Action

An entry action opens or adds exposure. Every confirmed entry action creates a
new logical position unit, even if OKX merges it into an existing net position.

Order submission first creates a `pending_open` unit with no allocated
quantity. Each confirmed fill is recorded once by its exchange fill id and
increases that unit's opened/remaining quantity. Multiple fills calculate a
weighted unit entry price without merging the unit into earlier entries.

Before either an entry or close is sent, the logical unit stores a unique OKX
client order ID (`clOrdId`). The eventual exchange order ID is linked by order
response, authenticated order lookup, or fill. A restart therefore cannot lose
an accepted order in the gap between exchange acceptance and local response
persistence. If OKX has no matching order after the stale threshold, the
prepared entry fails or the prepared close returns to `open`.

## Exit Action

An exit action reduces or closes one logical position unit according to its own
rules. In live mode, Maybech must place and confirm the exchange close/reduce
order before marking that unit closed.

Signal-triggered exits are automatic and must not wait for operator input after
their persisted condition matches. Manual close commands are separate and
require explicit operator confirmation. Both paths use reduce-only orders,
atomic `open -> closing` claims, and confirmed fill allocation.

A manual partial reduce claims an exact quantity smaller than the remaining
unit, using `open -> reducing`. Confirmed partial fills reduce the unit but keep
the intent pending until its requested quantity is filled or the order becomes
terminal. The owned stop is then restored at the exact confirmed remainder.
Response-loss and restart recovery reuse the original client order id.

## Break-Even Operation

A break-even operation moves risk on a logical position unit so the stop level
protects the entry price or better after sufficient favorable movement. It must
be tracked per logical unit, not only per OKX net position.

The confirmed operation accepts a zero-to-five-percent protected-profit offset.
Long targets round upward to the next valid tick and short targets round
downward, so precision normalization cannot weaken the requested protection.
Current price must already be beyond the target. The resulting stop change uses
the owned protection amend intent and records entry, target, observed price,
offset, and application time on the condition and durable audit event.

## Audit Event

An audit event records what happened and why. Strategy triggers, blocked actions,
entry orders, exit orders, rule edits, reconciliation changes, and manual
operator actions should all produce audit events.

Related decision, submission, order, fill, trade, and logical-position events
must retain one correlation id. An order submission response is not a confirmed
fill; allocation state becomes authoritative only from exchange-confirmed
execution evidence.

Allocation writes must be atomic with parent quantity updates. Re-delivery of
the same fill is idempotent; reusing one fill id with different content is a
conflict rather than a correction.

## Protective Stop Ownership

Every active logical unit with remaining real exposure owns exactly one active
OKX protective algo. Attached entry protection and standalone imported/recovery
stops share the same persisted lifecycle: `active`, `amending`, `canceling`,
`canceled`, `triggered`, `exhausted`, or `failed`.

When OKX triggers the algo, its `algoId` or `algoClOrdId` identifies the logical
unit before the resulting normal order fill is allocated. A software/manual
close first cancels and proves removal of that unit's exact algo. Unknown close
acceptance retains the same client order intent for retry/recovery. If the close
does not exist or terminates with remaining quantity, protection is re-armed at
the remaining size before the unit returns to normal management.

An edit to the enabled protected stop is not an ordinary rule edit. It owns a
durable amend intent and moves protection through `active -> amending -> active`
only after the old algo identity and the amended pending algo are both proven.
The new close-condition value is published only after exchange verification.
If OKX still proves the old stop, the old rule remains authoritative; an
ambiguous outcome marks protection failed and disables entries. Generic rule
mutation cannot alter or delete an owned active stop.
Protection verification always compares its quantity with the logical unit's
remaining exposure as well as the exchange algo. A stale persisted size cannot
prove that a unit is fully protected.
