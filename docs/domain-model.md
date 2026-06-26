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
- close conditions: stop-loss, take-profit, break-even, trailing, manual review
- current status: planned, pending_open, open, reducing, closing, closed, failed
- audit events and exchange order references

The first persistent implementation lives in `src/trading/logical_position_store.py`.
It stores logical units in SQLite and can backfill compatibility records from
existing `TradeStore` trades. That is a bridge toward the final lifecycle model,
not the complete reconciliation/execution layer.

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

## Exit Action

An exit action reduces or closes one logical position unit according to its own
rules. In live mode, Maybech must place and confirm the exchange close/reduce
order before marking that unit closed.

## Break-Even Operation

A break-even operation moves risk on a logical position unit so the stop level
protects the entry price or better after sufficient favorable movement. It must
be tracked per logical unit, not only per OKX net position.

## Audit Event

An audit event records what happened and why. Strategy triggers, blocked actions,
entry orders, exit orders, rule edits, reconciliation changes, and manual
operator actions should all produce audit events.
