# Maybech Project Charter

## Runtime Mode Contract

Maybech has four operator-facing modes. Simulation is the default and never
creates exchange orders. Demo is the mandatory execution-validation stage and
routes the real order lifecycle only to OKX Demo. Live Safe uses production
credentials for inspection, recovery, and reconciliation while order submission
stays disarmed. Live Armed is the only production order-capable mode and requires
explicit selection, `MAYBECH_ARM_ORDERS=1`, and the complete live preflight.

## Mission

Maybech is a local-first OKX perpetual trading workspace for signal-driven
strategy management and independent position management. The product goal is
not "one black-box bot." The goal is an operator-assist system that lets the
user define market signals, bind those signals to opening and closing actions,
watch every action with clear evidence, and safely manage each position unit
even when OKX represents several fills as one merged exchange position.

The completion target is dependable real-money backend operation fully exposed
through the frontend. A backend capability is not product-complete while its
state, evidence, and required operator controls are unavailable in the browser.

## Core Product Areas

### Strategy Management

Strategies are pre-position plans. A strategy watches one or more signals and,
when its entry conditions pass risk checks, creates a new position-management
unit. Strategy records must explain:

- what market and instrument they watch
- which signal expression triggered
- which side and size they intend to open
- which initial stop-loss, take-profit, and break-even rules are attached
- whether the action was blocked, simulated, manually reviewed, or executed

### Position Management

Positions are post-entry management units. They are not the same thing as an
OKX net position. OKX may merge repeated short or long swap entries into one
exchange-level position with averaged entry price. Maybech must still treat
each open/add action as its own logical position unit so stop-loss,
take-profit, break-even, reduce, and close rules can be evaluated independently.

Each logical position unit may optionally link back to the strategy that created
it. Manual positions can also exist without a strategy link, but they still need
editable close conditions and a clear audit trail.

### Signal Engine

Signals are the shared core for both strategy entries and position exits.
Examples:

- BTC price rises above a configured level
- BTC drops rapidly within a configured time window
- the traded instrument crosses a level
- current candle volume expands relative to prior candles
- composite expressions such as `A AND B`, `A OR B`, or grouped conditions

Signals should produce structured evidence, not only booleans. A user should be
able to inspect why a strategy opened or why a position closed.

## Required UI Shape

The dashboard should be organized around at least two first-class pages:

- Strategy Management: define, review, enable, disable, and inspect pre-position
  strategies and their signal expressions.
- Position Management: inspect logical position units, edit exit conditions,
  view current risk, and see every unit independently even when OKX merges the
  underlying exchange position.

The Position Management page should eventually show a compact visual K-line
context for each position unit, including recent candles, entry price, current
price, stop-loss, take-profit, and break-even levels.

## Safety Principles

- Simulation remains the default.
- Live execution requires explicit startup and arming.
- Close-order execution must be confirmed against OKX before Maybech marks a
  live logical position unit as closed.
- API endpoints that can affect trading must remain local-only until
  authentication and operator authorization exist.
- Runtime and docs must make the current safety limits visible.

## Documentation Contract

Use these docs as the source of truth:

- `docs/project-charter.md`: product goal and major product areas.
- `docs/domain-model.md`: concept names and relationships.
- `docs/system-direction.md`: architecture direction and refactor priorities.
- `docs/runtime-status.md`: current runtime behavior and API payload notes.
- `docs/api-spec.md`: intended API surface and contract direction.
- `docs/ui-direction.md`: target dashboard pages and interaction requirements.
- `toImprove.md`: active engineering priorities.

Raw historical notes may stay under `docs/handWrittenByTheLord/`, but future
agents should update the canonical docs above instead of relying on chat memory.
