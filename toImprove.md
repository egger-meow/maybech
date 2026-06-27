# To Improve

This file tracks the main improvement points agents should keep visible while
working on Maybech. Maintain at least three active items at all times.

## Current Top Priorities

1. Handle exchange order cancellation, rejection, and timeout so `pending_open`
   and `closing` units recover safely without duplicate submissions.
2. Add authenticated private OKX order websocket events for low-latency fills,
   cancellations, and unfilled remainders while retaining REST fill catch-up.
3. Normalize OKX contract/lot units and validate instrument minimum size and
   precision before open or reduce-only order submission.
4. Add authentication and operator authorization before exposing any service
   control or trading-control endpoint beyond localhost.
5. Move runtime state that must survive restarts from in-memory snapshots toward
   structured persistence with explicit retention rules.
6. Replace timestamp-only audit pagination with a stable opaque cursor and add
   retention/compaction so frequent evaluations cannot grow SQLite unbounded.
7. Consolidate dependency metadata so `requirements.txt`, `pyproject.toml`, and
   `uv.lock` no longer describe different dependency sources of truth.

## Maintenance Rules

- Read this file before making code or docs changes.
- Update the list whenever an item is completed, replaced, or made obsolete.
- Keep each item actionable and tied to concrete files, commands, or behavior.
- Preserve at least three current priorities so the next contributor has a clear
  direction.
