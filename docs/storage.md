# Storage And Schema Management

Maybech currently uses SQLite for local-first runtime persistence. This fits the
personal operator-assist target because it is simple to run on Windows, easy to
backup, and does not require a separate database service.

## Current Stores

- `src/trading/trade_store.py`: trades and trade rule groups.
- `src/trading/logical_position_store.py`: logical position units, immutable
  execution-order and protective-algo ownership, allocation events, and
  per-unit close conditions.
- `src/trading/strategy_store.py`: persisted strategy definitions and signal
  expression records.
- `src/trading/audit_event_store.py`: durable action, decision, and evaluation
  evidence with filters for event type, source, logical position, and trade.
- `src/trading/account_risk.py`: the singleton account risk envelope and live
  entry approval logic.
- `src/trading/instrument_metadata.py`: cached OKX tradable-instrument metadata
  for selectors and contract/display-unit conversion.
- `src/trading/sqlite_schema.py`: shared SQLite connection configuration and
  schema migration ledger helpers.

All stores use `MAYBECH_DB_PATH` when no explicit constructor path is supplied.
The default is `data/trades.db`.

## Schema Management Rule

SQLite schema changes must be versioned. New stores should use
`src/trading/sqlite_schema.py` to maintain the shared `schema_migrations` table
with a component name and monotonically increasing version. Do not rely only on
scattered `CREATE TABLE IF NOT EXISTS` statements without recording which schema
version was applied.

The first versioned components are `trade_store` in `TradeStore`,
`logical_positions` in `LogicalPositionStore`, `strategies` in
`StrategyStore`, and `audit_events` in `AuditEventStore`.
`logical_positions` is at schema version 6. Version 5 adds immutable parent and
close order links plus deferred allocation state. Version 6 adds one
`logical_position_protections` owner record per unit, with unique OKX algo,
algo-client, and triggered child-order identities plus explicit lifecycle
state. `audit_events` is at version 2,
`strategies` is at version 3, `account_risk` is at version 2 after adding the
default-disabled entry-control singleton, and `trade_store` and
`execution_cursors` are at version 1. `instrument_metadata` is at version 1.
Future changes should add explicit migration steps instead of editing existing
schema assumptions in place.

Runtime ownership is separate from SQLite schema state. Every default runtime
holds an OS file lock for the normalized database path; live mode also locks a
hashed OKX account scope. Lock files contain owner metadata only and are not
persistence or migration records.

Read-only API replicas do not acquire execution ownership and cannot mutate
state or stream in-memory events. Their request-scoped SQLite connections open
with `mode=ro`, enable `PRAGMA query_only`, and skip schema initialization, so
the database rejects accidental writes in addition to HTTP method filtering.
This separates horizontal read capacity from the single execution leader
without pretending SQLite supports distributed multi-host writes. A future
PostgreSQL migration can replace the shared storage boundary while preserving
one account-scoped execution leader.

## ORM / Migration Options

Prisma is not planned while the Python backend owns persistence. Adding a
TypeScript ORM beside Python stores would create two schema authorities. Prisma
should only be reconsidered if a TypeScript service becomes the database owner.
For this repo's current shape, the supported direction is:

- Keep lightweight hand-written SQLite migrations for the near term.
- Move to SQLModel or SQLAlchemy plus Alembic when models and migrations become
  too large for hand-written SQL.
- Generate frontend types from FastAPI OpenAPI separately; do not use Prisma
  just to solve frontend typing.

## Requirements For Future Persistence Work

- Every table-owning module needs a schema version.
- Every runtime SQLite connection should go through the shared helper so WAL,
  row mapping, and foreign-key checks are consistently enabled.
- Migrations must be idempotent and covered by tests.
- Confirmed allocation inserts and their logical-position quantity changes must
  commit in one transaction. External fill ids are immutable idempotency keys;
  conflicting payloads must fail rather than overwrite prior allocation data.
- Product-definition mutations and their audit events must commit in one
  transaction. A failed audit insert must leave strategy, signal-expression,
  and logical-position close-condition state unchanged.
- Runtime data should live under a clear runtime data path, not inside `src/`.
- Stores should keep domain concepts separate: strategies, signals, logical
  positions, allocations, and audit events should not collapse into one table.
