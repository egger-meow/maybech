# To Improve

Only current blockers to dependable real-money operation belong here.

## Current Priorities

1. Replace the transitional `MomentumStrategy` daemon path with direct execution
   of persisted generic signal expressions, then remove `momentum.py`,
   `volume_price_gap`, optimizer coupling, obsolete tests, and hardcoded UI IDs.
2. Persist OKX fill-history catch-up cursors so more than 100 fills or a restart
   cannot skip an execution.
3. Add authenticated private OKX order events for latency while retaining REST
   catch-up as the correctness layer.
4. Complete demo-account open, partial-fill, cancellation, automatic close, and
   restart recovery verification before arming a live account.

Do not add general cleanup or speculative features. Add an item only when it is
a concrete correctness or safety blocker, and remove it when completed.
