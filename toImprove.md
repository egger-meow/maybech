# To Improve

Only current blockers to dependable real-money operation belong here.

## Current Priorities

1. Complete demo-account open, partial-fill, cancellation, automatic close, and
   restart recovery verification before arming a live account.
2. Block new entries when OKX net exposure cannot be reconciled to Maybech
   logical units, with an explicit import/recovery path for external positions.
3. Add verified SQLite backup/restore tooling and require a current recoverable
   backup before live schema migration or real-account startup.

Do not add general cleanup or speculative features. Add an item only when it is
a concrete correctness or safety blocker, and remove it when completed.
