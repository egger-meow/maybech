# UI Direction

The frontend should be a control surface for repeated trading operations, not a
marketing site. It should optimize for clarity, inspection, and fast edits.

## Primary Navigation

The dashboard should make these pages first-class:

- Strategy Management
- Position Management
- Events / Audit Log
- Account / Runtime Status
- Backtesting / Research

Strategy Management and Position Management are the two core product pages.

## Strategy Management Page

Purpose: manage pre-position plans.

Required capabilities:

- list strategies with enabled state, target instrument, side, readiness, and
  latest decision
- create and edit strategy entry signal expressions
- configure default stop-loss, take-profit, break-even, and size policy
- show BTC regime filters and risk blocks
- run backtests or display latest backtest pass/fail state
- inspect recent decisions with signal evidence and action result
- clearly separate disabled, ready, dry-run active, and live armed states

## Position Management Page

Purpose: manage post-entry logical position units.

Required capabilities:

- list each logical position unit independently
- show linked OKX net position when several logical units are merged by OKX
- edit per-unit stop-loss, take-profit, trailing, and break-even conditions
- request close/reduce for one logical unit
- show current price, unrealized PnL estimate, distance to rules, and risk
- expose source strategy tag when a strategy created the unit
- support manual position units with no strategy tag

The page should make it obvious that "OKX position" and "Maybech logical
position unit" are different concepts.

## K-Line Position Visualization

Each logical position unit should eventually have a compact candle view:

- recent candles for the instrument
- entry price for the unit
- current price
- stop-loss line
- take-profit line
- break-even line when armed or applied
- markers for entry, partial reduce, close, or failed order attempts

The visual should be dense and inspectable. It should not hide important levels
behind decoration.

## Interaction Rules

- Any control that can affect live trading needs an explicit confirmation path.
- Rule edits should show unsaved vs saved state.
- Dry-run, live-unarmed, and live-armed modes must be visually distinct.
- If API data is stale or missing, the UI must say so instead of pretending the
  state is current.
- Text and numbers should fit on small screens without overlapping.
