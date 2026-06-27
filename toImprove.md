# To Improve

Only current blockers to dependable real-money operation belong here.

## Current Priorities

1. Add authenticated private OKX order events for latency while retaining REST
   catch-up as the correctness layer.
2. Add an operator entry kill switch that cancels Maybech pending entry orders
   while leaving automatic reduce-only position closes active.
3. Complete demo-account open, partial-fill, cancellation, automatic close, and
   restart recovery verification before arming a live account.

Do not add general cleanup or speculative features. Add an item only when it is
a concrete correctness or safety blocker, and remove it when completed.
