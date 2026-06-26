# Storage And Schema Management

Maybech currently uses SQLite for local-first runtime persistence. This fits the
personal operator-assist target because it is simple to run on Windows, easy to
backup, and does not require a separate database service.

## Current Stores

- `src/trading/trade_store.py`: trades and trade rule groups.
- `src/trading/logical_position_store.py`: logical position units and
  allocation events.
- `src/trading/strategy_store.py`: persisted strategy definitions and signal
  expression records.
- `src/trading/sqlite_schema.py`: shared SQLite connection configuration and
  schema migration ledger helpers.

## Schema Management Rule

SQLite schema changes must be versioned. New stores should use
`src/trading/sqlite_schema.py` to maintain the shared `schema_migrations` table
with a component name and monotonically increasing version. Do not rely only on
scattered `CREATE TABLE IF NOT EXISTS` statements without recording which schema
version was applied.

The first versioned components are `trade_store` in `TradeStore`,
`logical_positions` in `LogicalPositionStore`, and `strategies` in
`StrategyStore`. Future changes should add explicit migration steps instead of
editing existing schema assumptions in place.

## ORM / Migration Options

Prisma is possible, but it is not the default fit while the backend is
Python-owned. Prisma would make more sense if a TypeScript service became the
owner of persistence. For this repo's current shape, likely better options are:

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
- Runtime data should live under a clear runtime data path, not inside `src/`.
- Stores should keep domain concepts separate: strategies, signals, logical
  positions, allocations, and audit events should not collapse into one table.
