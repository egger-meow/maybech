# To Improve

Only current blockers to dependable real-money operation belong here.

## Current Priorities

1. Complete demo-account open, partial-fill, cancellation, automatic close, and
   restart recovery verification before arming a live account.
2. Reject nonzero OKX per-order `sCode` responses and verify exchange-side
   protective stops are active before treating a strategy entry as submitted.
3. Keep imported or recovered units entry-blocking until side-consistent
   exchange protection has been attached and verified on OKX.

Do not add general cleanup or speculative features. Add an item only when it is
a concrete correctness or safety blocker, and remove it when completed. Always keep the priority items in order and don't add new priority items unless it is really important. ALWAYS DO THE HIGHER PRIORITY ITEM FIRST.
