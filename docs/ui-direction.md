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

A future top-right Demo / Real selector must make the active environment
visually obvious but must not arm orders, enable strategies, or open the entry
gate. Demo maps to `DEMO_OKX_*` plus `OKX_FLAG=1`; Real maps to `OKX_*` plus
`OKX_FLAG=0`. Until separately locked databases, account scopes, ports, and
single leaders can be proven, changing environment should require a restart
rather than suggesting safe one-click concurrent operation.

## Notification Management Page

- First-class Traditional Chinese page for LINE and Gmail channel readiness.
- Show persisted last success/failure, failure class, retry time, consecutive
  failures, service state, and queued lifecycle-event count honestly.
- Require a visible confirmation before a real test message is sent.
- Never return or render tokens, passwords, user IDs, or destination addresses.

## Risk Limits Page

- First-class Traditional Chinese editor for the persisted account envelope.
- Show the current enabled state, backend update time, and entry-gate state.
- Keep unsaved values visibly distinct from the last backend response.
- Validate notional, gross exposure, and leverage before submission.
- Disable mutation unless entries are confirmed off and the operator explicitly
  confirms the high-risk overwrite; successful saves state clearly that they do
  not arm orders or enable strategy entries.

## Strategy Management Page

Purpose: manage pre-position plans.

Required capabilities:

- select one or more cached, currently tradable OKX instruments through a
  searchable combobox; typed search filters options but cannot submit an
  arbitrary instrument id

- list strategies with enabled state, target instrument, side, readiness, and
  latest decision
- create and edit strategy entry signal expressions
- configure default stop-loss, take-profit, break-even, and size policy
- show BTC regime filters and risk blocks
- run backtests or display latest backtest pass/fail state
- inspect recent decisions with signal evidence and action result
- clearly separate disabled, ready, dry-run active, and live armed states
- configure execution delay as disabled/0 seconds or an explicit number of
  seconds, and show restart-safe pending actions with due time and correlation id

## Position Management Page

Purpose: manage post-entry logical position units.

Required capabilities:

- use the same cached searchable instrument selector for manual opens and rule
  targets, with `self` first and BTC immediately available for cross-market
  conditions

- list each logical position unit independently
- show linked OKX net position when several logical units are merged by OKX
- edit per-unit stop-loss, take-profit, trailing, and break-even conditions
- request close/reduce for one logical unit
- show current price, unrealized PnL estimate, distance to rules, and risk
- show operator-facing base quantity first while retaining the derived OKX API
  contract count; reduce input uses base quantity and is converted back through
  cached metadata before confirmation
- show side-aware estimated USDT loss/profit for a rule with one absolute price;
  composite or non-price expressions must state that a single estimate is not
  available
- expose source strategy tag when a strategy created the unit
- support manual position units with no strategy tag
- create a Dry-run manual unit from a searchable cached instrument selector,
  prefill the entry limit only from a fresh market price, and show display
  quantity, derived OKX contracts, notional, and stop PnL before confirmation
- show owned protection state, stop level, protected quantity, algo identity,
  and triggered child-order identity without hiding failed/canceled states
- guide clear recovery units through a red adoption panel with a side-correct
  stop and estimated USDT impact; keep manual review visible until OKX proves
  the exact independently owned protection, and never offer this shortcut for
  ambiguous external reductions

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
- The runtime banner must list each live-readiness requirement separately:
  account risk amounts/enabled state, cached instruments, per-strategy API
  size, slippage, side-correct protective stop, derivatives account level, and
  `net_mode`. A missing risk record must name all missing fields.
- If API data is stale or missing, the UI must say so instead of pretending the
  state is current.
- Text and numbers should fit on small screens without overlapping.

## Current First-Loop Implementation

The Strategy Management and Position Management pages now implement the first
usable loop against real backend APIs and generated OpenAPI types. The strategy
page supports create/edit/inspect/enable/disable/delete, primary and child
signals, default position rules, nested AND/OR expression groups, decision
evidence, and explicit saved/unsaved state. The position page keeps Maybech
logical units separate from OKX net snapshots, renders K-line overlays, edits
per-unit rules, routes owned-stop changes through confirmed exchange amendment,
supports break-even and protection retry, and confirms exact reduce/full-close
commands. Missing or stale runtime/market data is shown as unavailable or stale
rather than replaced with synthetic state.

The dashboard provides a guarded real-money guide covering environment modes,
separate demo/production credentials, OKX account and IP-whitelist checks,
account risk, live arming, strategy enablement, and the separate entry gate. It
states prominently that `MAYBECH_ARM_ORDERS=0` prevents live order placement.
