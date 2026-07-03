# Position Rule and Market Analysis Phase Audit

Audit date: 2026-07-04

This document is the completion evidence for the activated phase formerly held
in `toImprove.md`. The active checklist is removed only after every requirement
below is implemented and verified.

## Requirement Audit

| Requirement | Authoritative implementation evidence | Verification evidence | Result |
|---|---|---|---|
| One typed, revision-protected rule model for strategy defaults and logical-position overrides | `src/trading/position_rule_model.py`, `strategy_store.py`, `logical_position_store.py`; guarded strategy/position APIs | `test_position_rule_model.py`, `test_strategy_store.py`, `test_rule_promotion.py`, API stale-revision tests | Pass |
| Fixed-loss and structure-anchored stops never exceed allowed loss after tick/lot rounding and modeled costs | `InstrumentSizer.quote_risk` reserves entry/exit fees and two-sided slippage, rounds stops/sizes conservatively, and rejects excess loss | `test_instrument_metadata.py`, `test_rule_promotion.py` | Pass |
| Fixed/evidence/percent targets, staged reductions, and a running remainder | Confirmed-entry materialization and allocation-confirmed one-shot reductions | `test_strategy_runtime.py`, `test_execution_allocation.py`, `test_position_manager_service.py`, `test_position_rule_model.py` | Pass |
| Fee/slippage-adjusted, persisted, restart-safe break-even with confirmed live amendments | Dedicated break-even calculation and lifecycle in position manager/protection services | Break-even long/short, restart, unfavorable-price, dry-run, and live-amend tests | Pass |
| Optional monotonic trailing and separate trailing take-profit semantics | Persisted activation/water/candidate state, observation freshness gate, confirmed stop amend, retracement close/reduce | Trailing materialization, monotonic restart, stale fail-closed, live amendment, and retracement tests | Pass |
| Bounded, incremental, freshness-aware Support/Resistance evidence that remains research-only | Bounded candle cache/window state, overlap refresh, incremental pivot scan, explicit invalidation and quality response | `test_support_resistance.py` covers equivalence, stale, missing, duplicate, API failure, cache and bounds | Pass |
| Explicit operator states and guarded research promotion | Analysis UI derives fresh/partial/stale/unavailable/proposed/invalidated/manual-review; Position UI shows armed/applied/trailing states; APIs reject unreviewable research | Frontend gates plus `test_rule_promotion.py` and typed API tests | Pass |
| Typed overlays from rules and confirmed execution evidence | Position chart API emits entry/current/stop/target/break-even/trailing/execution overlays | `test_api_returns_logical_position_chart_overlays` and generated contract check | Pass |
| Strategy, Position, and Analysis workflows use the shipped model | Purpose-specific editors for fixed/percent/staged/break-even/trailing plus revision-bound promotion | TypeScript, ESLint, production build, API tests | Pass |

## Final Gates

- Backend: `uv run pytest --cov=src --cov-report=term`
  - 384 passed, 12 credentialed integration tests skipped, 83% total coverage.
- Frontend: `npm run verify`
  - OpenAPI contract check, ESLint, TypeScript, and Next.js production build passed.
- Contract artifacts: `docs/openapi.json` and
  `frontend/lib/generated/api-types.ts` are current.
- Restart and partial-fill behavior is covered by focused break-even, trailing,
  strategy-delay, execution-fill, and staged-reduction tests.

The skipped OKX integration tests require explicitly configured exchange
credentials. Prior bounded demo and production lifecycle evidence remains in
`docs/build-status.md`; no credentialed order was required for this phase audit.
