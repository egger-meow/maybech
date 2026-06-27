"""SQLite persistence for Maybech logical position units.

Logical position units are the product-facing management objects for open/add
actions. They are intentionally separate from OKX net positions because OKX may
merge repeated entries for the same instrument and side.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator, Literal
from uuid import uuid4

from src.config.settings import settings
from src.trading.trade_store import TradeRecord
from src.trading.sqlite_schema import (
    applied_schema_versions,
    configure_connection,
    initialize_schema,
    record_schema_version,
)


LogicalPositionSource = Literal["strategy", "manual", "import", "recovery", "unknown"]
LogicalPositionStatus = Literal[
    "planned",
    "pending_open",
    "open",
    "reducing",
    "closing",
    "closed",
    "failed",
]
AllocationAction = Literal["open", "reduce", "close", "fee", "adjustment"]
CloseConditionPurpose = Literal[
    "stop_loss",
    "take_profit",
    "trailing",
    "break_even",
    "manual_review",
    "exit",
]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _json_loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return fallback


class AllocationConflictError(ValueError):
    """Raised when one external fill id is reused for different allocation data."""


class LogicalPositionAllocation:
    """Execution-confirmed quantity allocation for a logical unit."""

    __slots__ = (
        "id",
        "position_id",
        "action",
        "quantity",
        "price",
        "fee",
        "exchange_order_id",
        "reason",
        "created_at",
        "metadata_json",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        position_id: str,
        action: AllocationAction,
        quantity: float = 0.0,
        price: float | None = None,
        fee: float | None = None,
        exchange_order_id: str = "",
        reason: str = "",
        created_at: str = "",
        metadata_json: str = "{}",
    ) -> None:
        self.id = id or uuid4().hex[:12]
        self.position_id = position_id
        self.action = action
        self.quantity = quantity
        self.price = price
        self.fee = fee
        self.exchange_order_id = exchange_order_id
        self.reason = reason
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()
        self.metadata_json = metadata_json

    def to_dict(self) -> dict:
        return {attr: getattr(self, attr) for attr in self.__slots__}

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "LogicalPositionAllocation":
        return cls(**{key: row[key] for key in row.keys()})


class LogicalPositionRecord:
    """Persistent record for one independent position-management unit."""

    __slots__ = (
        "id",
        "source",
        "strategy_id",
        "trade_id",
        "inst_id",
        "side",
        "opened_quantity",
        "remaining_quantity",
        "entry_price",
        "entry_time",
        "status",
        "exchange_order_id",
        "client_order_id",
        "exchange_position_key",
        "metadata_json",
        "created_at",
        "updated_at",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        source: LogicalPositionSource = "unknown",
        strategy_id: str = "",
        trade_id: str | None = None,
        inst_id: str = "",
        side: str = "",
        opened_quantity: float | None = None,
        remaining_quantity: float | None = None,
        entry_price: float = 0.0,
        entry_time: str = "",
        status: LogicalPositionStatus = "open",
        exchange_order_id: str = "",
        client_order_id: str = "",
        exchange_position_key: str = "",
        metadata_json: str = "{}",
        created_at: str = "",
        updated_at: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.id = id or uuid4().hex[:12]
        self.source = source
        self.strategy_id = strategy_id
        self.trade_id = trade_id
        self.inst_id = inst_id
        self.side = side
        self.opened_quantity = opened_quantity
        self.remaining_quantity = remaining_quantity
        self.entry_price = entry_price
        self.entry_time = entry_time or now
        self.status = status
        self.exchange_order_id = exchange_order_id
        self.client_order_id = client_order_id
        self.exchange_position_key = exchange_position_key
        self.metadata_json = metadata_json
        self.created_at = created_at or now
        self.updated_at = updated_at or now

    def to_dict(self) -> dict:
        return {attr: getattr(self, attr) for attr in self.__slots__}

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "LogicalPositionRecord":
        return cls(**{key: row[key] for key in row.keys()})

    @classmethod
    def from_trade(cls, trade: TradeRecord) -> "LogicalPositionRecord":
        source: LogicalPositionSource = "strategy" if trade.strategy_id else "manual"
        status_map: dict[str, LogicalPositionStatus] = {
            "pending_open": "pending_open",
            "open": "open",
            "closed": "closed",
            "failed": "failed",
        }
        status = status_map.get(trade.status, "open")
        trade_metadata = _json_loads(trade.metadata_json, {})
        metadata = trade_metadata if isinstance(trade_metadata, dict) else {}
        metadata = {**metadata, "backfilled_from_trade": True}
        return cls(
            id=trade.id,
            source=source,
            strategy_id=trade.strategy_id,
            trade_id=trade.id,
            inst_id=trade.inst_id,
            side=trade.side,
            opened_quantity=0.0 if trade.status == "pending_open" else None,
            remaining_quantity=0.0 if trade.status in {"pending_open", "closed", "failed"} else None,
            entry_price=trade.entry_price,
            entry_time=trade.entry_time,
            status=status,
            exchange_order_id=str(metadata.get("exchange_order_id") or ""),
            client_order_id=str(metadata.get("client_order_id") or ""),
            metadata_json=_json_dumps(metadata),
            created_at=trade.entry_time,
            updated_at=trade.exit_time or trade.entry_time,
        )


class LogicalPositionCloseCondition:
    """First-class signal expression that manages one logical position unit."""

    __slots__ = (
        "id",
        "position_id",
        "purpose",
        "expression_json",
        "enabled",
        "metadata_json",
        "created_at",
        "updated_at",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        position_id: str,
        purpose: CloseConditionPurpose | str = "exit",
        expression_json: str = "{}",
        enabled: bool = True,
        metadata_json: str = "{}",
        created_at: str = "",
        updated_at: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.id = id or uuid4().hex[:12]
        self.position_id = position_id
        self.purpose = purpose
        self.expression_json = expression_json
        self.enabled = enabled
        self.metadata_json = metadata_json
        self.created_at = created_at or now
        self.updated_at = updated_at or now

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "LogicalPositionCloseCondition":
        data = {key: row[key] for key in row.keys()}
        data["enabled"] = bool(data["enabled"])
        return cls(**data)

    @property
    def expression(self) -> dict[str, Any]:
        value = _json_loads(self.expression_json, {})
        return value if isinstance(value, dict) else {}

    @property
    def metadata(self) -> dict[str, Any]:
        value = _json_loads(self.metadata_json, {})
        return value if isinstance(value, dict) else {}

    def to_dict(self) -> dict:
        data = {attr: getattr(self, attr) for attr in self.__slots__}
        data["expression"] = self.expression
        data["metadata"] = self.metadata
        del data["expression_json"]
        del data["metadata_json"]
        return data


_SCHEMA_COMPONENT = "logical_positions"
_SCHEMA_VERSION = 4


_SCHEMA = """
CREATE TABLE IF NOT EXISTS logical_positions (
    id                    TEXT PRIMARY KEY,
    source                TEXT NOT NULL DEFAULT 'unknown',
    strategy_id           TEXT NOT NULL DEFAULT '',
    trade_id              TEXT,
    inst_id               TEXT NOT NULL DEFAULT '',
    side                  TEXT NOT NULL DEFAULT '',
    opened_quantity       REAL,
    remaining_quantity    REAL,
    entry_price           REAL NOT NULL DEFAULT 0.0,
    entry_time            TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'open',
    exchange_position_key TEXT NOT NULL DEFAULT '',
    metadata_json         TEXT NOT NULL DEFAULT '{}',
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS logical_position_allocations (
    id                TEXT PRIMARY KEY,
    position_id       TEXT NOT NULL,
    action            TEXT NOT NULL,
    quantity          REAL NOT NULL DEFAULT 0.0,
    price             REAL,
    fee               REAL,
    exchange_order_id TEXT NOT NULL DEFAULT '',
    reason            TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (position_id) REFERENCES logical_positions(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS logical_position_close_conditions (
    id              TEXT PRIMARY KEY,
    position_id     TEXT NOT NULL,
    purpose         TEXT NOT NULL DEFAULT 'exit',
    expression_json TEXT NOT NULL DEFAULT '{}',
    enabled         INTEGER NOT NULL DEFAULT 1,
    metadata_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (position_id) REFERENCES logical_positions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_logical_positions_status
    ON logical_positions(status);
CREATE INDEX IF NOT EXISTS idx_logical_positions_strategy
    ON logical_positions(strategy_id);
CREATE INDEX IF NOT EXISTS idx_logical_positions_trade
    ON logical_positions(trade_id);
CREATE INDEX IF NOT EXISTS idx_logical_positions_inst_side
    ON logical_positions(inst_id, side);
CREATE INDEX IF NOT EXISTS idx_logical_position_allocations_position
    ON logical_position_allocations(position_id);
CREATE INDEX IF NOT EXISTS idx_logical_position_close_conditions_position
    ON logical_position_close_conditions(position_id);
CREATE INDEX IF NOT EXISTS idx_logical_position_close_conditions_enabled
    ON logical_position_close_conditions(enabled);
"""


class LogicalPositionStore:
    """SQLite-backed logical position persistence."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.MAYBECH_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            initialize_schema(
                conn,
                schema_sql=_SCHEMA,
                component=_SCHEMA_COMPONENT,
                version=2,
            )
            versions = applied_schema_versions(conn, component=_SCHEMA_COMPONENT)
            if 3 not in versions:
                self._migrate_v3(conn)
            if _SCHEMA_VERSION not in versions:
                self._migrate_v4(conn)

    @staticmethod
    def _migrate_v3(conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(logical_positions)").fetchall()
        }
        if "exchange_order_id" not in columns:
            conn.execute(
                "ALTER TABLE logical_positions "
                "ADD COLUMN exchange_order_id TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_logical_positions_exchange_order "
            "ON logical_positions(exchange_order_id)"
        )
        record_schema_version(conn, component=_SCHEMA_COMPONENT, version=3)

    @staticmethod
    def _migrate_v4(conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(logical_positions)").fetchall()
        }
        if "client_order_id" not in columns:
            conn.execute(
                "ALTER TABLE logical_positions "
                "ADD COLUMN client_order_id TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_logical_positions_client_order "
            "ON logical_positions(client_order_id) WHERE client_order_id != ''"
        )
        record_schema_version(conn, component=_SCHEMA_COMPONENT, version=4)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        configure_connection(conn)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save(self, position: LogicalPositionRecord) -> str:
        position.updated_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO logical_positions
                   (id, source, strategy_id, trade_id, inst_id, side,
                    opened_quantity, remaining_quantity, entry_price, entry_time,
                    status, exchange_order_id, client_order_id, exchange_position_key,
                    metadata_json, created_at,
                    updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    source = excluded.source,
                    strategy_id = excluded.strategy_id,
                    trade_id = excluded.trade_id,
                    inst_id = excluded.inst_id,
                    side = excluded.side,
                    opened_quantity = excluded.opened_quantity,
                    remaining_quantity = excluded.remaining_quantity,
                    entry_price = excluded.entry_price,
                    entry_time = excluded.entry_time,
                    status = excluded.status,
                    exchange_order_id = excluded.exchange_order_id,
                    client_order_id = excluded.client_order_id,
                    exchange_position_key = excluded.exchange_position_key,
                    metadata_json = excluded.metadata_json,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at""",
                (
                    position.id,
                    position.source,
                    position.strategy_id,
                    position.trade_id,
                    position.inst_id,
                    position.side,
                    position.opened_quantity,
                    position.remaining_quantity,
                    position.entry_price,
                    position.entry_time,
                    position.status,
                    position.exchange_order_id,
                    position.client_order_id,
                    position.exchange_position_key,
                    position.metadata_json,
                    position.created_at,
                    position.updated_at,
                ),
            )
        return position.id

    def get(self, position_id: str) -> LogicalPositionRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logical_positions WHERE id = ?",
                (position_id,),
            ).fetchone()
        return None if row is None else LogicalPositionRecord.from_row(row)

    def get_by_exchange_order_id(self, order_id: str) -> LogicalPositionRecord | None:
        if not order_id:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM logical_positions
                   WHERE exchange_order_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (order_id,),
            ).fetchone()
        return None if row is None else LogicalPositionRecord.from_row(row)

    def get_by_client_order_id(self, client_order_id: str) -> LogicalPositionRecord | None:
        if not client_order_id:
            return None
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM logical_positions
                   WHERE client_order_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (client_order_id,),
            ).fetchone()
        return None if row is None else LogicalPositionRecord.from_row(row)

    def list_pending_executions(self, *, limit: int = 500) -> list[LogicalPositionRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM logical_positions
                   WHERE (exchange_order_id != '' OR client_order_id != '')
                     AND status IN ('pending_open', 'open', 'reducing', 'closing')
                   ORDER BY updated_at
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [LogicalPositionRecord.from_row(row) for row in rows]

    def list(
        self,
        *,
        status: str | None = "open",
        strategy_id: str | None = None,
        limit: int = 100,
    ) -> list[LogicalPositionRecord]:
        query = "SELECT * FROM logical_positions WHERE 1=1"
        params: list[Any] = []
        if status and status != "all":
            query += " AND status = ?"
            params.append(status)
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        query += " ORDER BY entry_time DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [LogicalPositionRecord.from_row(row) for row in rows]

    def ensure_from_trade(self, trade: TradeRecord) -> LogicalPositionRecord:
        existing = self.get(trade.id)
        if existing is not None:
            return existing
        position = LogicalPositionRecord.from_trade(trade)
        self.save(position)
        return position

    def update_status(
        self,
        position_id: str,
        *,
        status: LogicalPositionStatus,
        remaining_quantity: float | None = None,
    ) -> LogicalPositionRecord | None:
        position = self.get(position_id)
        if position is None:
            return None
        position.status = status
        if remaining_quantity is not None:
            position.remaining_quantity = remaining_quantity
        self.save(position)
        return position

    def mark_pending_execution(
        self,
        position_id: str,
        *,
        status: LogicalPositionStatus,
        exchange_order_id: str,
        metadata: dict[str, Any],
    ) -> LogicalPositionRecord | None:
        position = self.get(position_id)
        if position is None:
            return None
        current_metadata = _json_loads(position.metadata_json, {})
        if not isinstance(current_metadata, dict):
            current_metadata = {}
        position.status = status
        position.exchange_order_id = exchange_order_id
        position.metadata_json = _json_dumps({**current_metadata, **metadata})
        self.save(position)
        return self.get(position_id)

    def claim_pending_execution(
        self,
        position_id: str,
        *,
        expected_status: LogicalPositionStatus,
        status: LogicalPositionStatus,
        client_order_id: str,
        metadata: dict[str, Any],
    ) -> LogicalPositionRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logical_positions WHERE id = ?",
                (position_id,),
            ).fetchone()
            if row is None:
                return None
            position = LogicalPositionRecord.from_row(row)
            current_metadata = _json_loads(position.metadata_json, {})
            if not isinstance(current_metadata, dict):
                current_metadata = {}
            updated_at = datetime.now(timezone.utc).isoformat()
            cursor = conn.execute(
                """UPDATE logical_positions SET
                   status = ?, exchange_order_id = '', client_order_id = ?,
                   metadata_json = ?, updated_at = ?
                   WHERE id = ? AND status = ?""",
                (
                    status,
                    client_order_id,
                    _json_dumps({**current_metadata, **metadata}),
                    updated_at,
                    position_id,
                    expected_status,
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get(position_id)

    def release_pending_execution(
        self,
        position_id: str,
        *,
        correlation_id: str,
        restore_status: LogicalPositionStatus,
    ) -> LogicalPositionRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logical_positions WHERE id = ?",
                (position_id,),
            ).fetchone()
            if row is None:
                return None
            position = LogicalPositionRecord.from_row(row)
            metadata = _json_loads(position.metadata_json, {})
            if not isinstance(metadata, dict) or metadata.get("correlation_id") != correlation_id:
                return position
            previous_order_id = str(metadata.get("previous_exchange_order_id") or "")
            metadata["execution_status"] = "submission_failed"
            conn.execute(
                """UPDATE logical_positions SET
                   status = ?, exchange_order_id = ?, client_order_id = '',
                   metadata_json = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    restore_status,
                    previous_order_id,
                    _json_dumps(metadata),
                    datetime.now(timezone.utc).isoformat(),
                    position_id,
                ),
            )
        return self.get(position_id)

    def link_exchange_order(
        self,
        position_id: str,
        *,
        client_order_id: str,
        exchange_order_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> LogicalPositionRecord | None:
        if not client_order_id or not exchange_order_id:
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM logical_positions "
                "WHERE id = ? AND client_order_id = ?",
                (position_id, client_order_id),
            ).fetchone()
            if row is None:
                return None
            current_metadata = _json_loads(row["metadata_json"], {})
            if not isinstance(current_metadata, dict):
                current_metadata = {}
            conn.execute(
                """UPDATE logical_positions SET
                   exchange_order_id = ?, metadata_json = ?, updated_at = ?
                   WHERE id = ? AND client_order_id = ?""",
                (
                    exchange_order_id,
                    _json_dumps({**current_metadata, **(metadata or {})}),
                    datetime.now(timezone.utc).isoformat(),
                    position_id,
                    client_order_id,
                ),
            )
        return self.get(position_id)

    def recover_client_order_intent(
        self,
        position_id: str,
        *,
        client_order_id: str,
        execution_status: str,
    ) -> LogicalPositionRecord | None:
        """Release a pre-submitted intent when OKX confirms no matching order."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logical_positions WHERE id = ? AND client_order_id = ? "
                "AND exchange_order_id = ''",
                (position_id, client_order_id),
            ).fetchone()
            if row is None:
                return self.get(position_id)
            position = LogicalPositionRecord.from_row(row)
            if position.status == "pending_open":
                recovered_status: LogicalPositionStatus = "failed"
            elif position.status in {"closing", "reducing"}:
                recovered_status = "open"
            else:
                return position
            metadata = _json_loads(position.metadata_json, {})
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.update(
                {
                    "execution_status": execution_status,
                    "recovered_client_order_id": client_order_id,
                    "recovered_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            conn.execute(
                """UPDATE logical_positions SET
                   status = ?, client_order_id = '', metadata_json = ?, updated_at = ?
                   WHERE id = ? AND client_order_id = ? AND exchange_order_id = ''""",
                (
                    recovered_status,
                    _json_dumps(metadata),
                    datetime.now(timezone.utc).isoformat(),
                    position_id,
                    client_order_id,
                ),
            )
        return self.get(position_id)

    def recover_terminal_order(
        self,
        position_id: str,
        *,
        exchange_order_id: str,
        order_state: str,
    ) -> LogicalPositionRecord | None:
        """Recover one pending unit after OKX confirms a terminal order state."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logical_positions WHERE id = ?",
                (position_id,),
            ).fetchone()
            if row is None:
                return None
            position = LogicalPositionRecord.from_row(row)
            if position.exchange_order_id != exchange_order_id:
                return position
            if position.status == "pending_open":
                recovered_status: LogicalPositionStatus = (
                    "open" if (position.opened_quantity or 0.0) > 0 else "failed"
                )
            elif position.status in {"closing", "reducing"}:
                recovered_status = (
                    "closed" if (position.remaining_quantity or 0.0) == 0 else "open"
                )
            elif position.status == "open":
                recovered_status = "open"
            else:
                return position

            metadata = _json_loads(position.metadata_json, {})
            if not isinstance(metadata, dict):
                metadata = {}
            metadata.update(
                {
                    "execution_status": "terminal_recovered",
                    "terminal_order_state": order_state,
                    "terminal_order_id": exchange_order_id,
                    "recovered_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            conn.execute(
                """UPDATE logical_positions SET
                   status = ?, exchange_order_id = '', client_order_id = '',
                   metadata_json = ?, updated_at = ?
                   WHERE id = ? AND exchange_order_id = ?""",
                (
                    recovered_status,
                    _json_dumps(metadata),
                    datetime.now(timezone.utc).isoformat(),
                    position_id,
                    exchange_order_id,
                ),
            )
        return self.get(position_id)

    def update_execution_tracking(
        self,
        position_id: str,
        *,
        exchange_order_id: str,
        execution_status: str,
        completed: bool,
    ) -> LogicalPositionRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logical_positions WHERE id = ?",
                (position_id,),
            ).fetchone()
            if row is None:
                return None
            position = LogicalPositionRecord.from_row(row)
            if position.exchange_order_id != exchange_order_id:
                return position
            metadata = _json_loads(position.metadata_json, {})
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["execution_status"] = execution_status
            if completed:
                metadata["completed_order_id"] = exchange_order_id
                metadata["completed_at"] = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """UPDATE logical_positions SET
                   exchange_order_id = ?, client_order_id = ?, metadata_json = ?, updated_at = ?
                   WHERE id = ? AND exchange_order_id = ?""",
                (
                    "" if completed else exchange_order_id,
                    "" if completed else position.client_order_id,
                    _json_dumps(metadata),
                    datetime.now(timezone.utc).isoformat(),
                    position_id,
                    exchange_order_id,
                ),
            )
        return self.get(position_id)

    def mark_order_cancel_requested(
        self,
        position_id: str,
        *,
        exchange_order_id: str,
    ) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM logical_positions "
                "WHERE id = ? AND exchange_order_id = ?",
                (position_id, exchange_order_id),
            ).fetchone()
            if row is None:
                return False
            metadata = _json_loads(row["metadata_json"], {})
            if not isinstance(metadata, dict):
                metadata = {}
            if metadata.get("cancel_requested_at"):
                return False
            metadata["cancel_requested_at"] = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE logical_positions SET metadata_json = ?, updated_at = ? "
                "WHERE id = ? AND exchange_order_id = ?",
                (
                    _json_dumps(metadata),
                    datetime.now(timezone.utc).isoformat(),
                    position_id,
                    exchange_order_id,
                ),
            )
        return True

    def is_order_cancel_requested(
        self,
        position_id: str,
        *,
        exchange_order_id: str,
    ) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM logical_positions "
                "WHERE id = ? AND exchange_order_id = ?",
                (position_id, exchange_order_id),
            ).fetchone()
        if row is None:
            return False
        metadata = _json_loads(row["metadata_json"], {})
        return isinstance(metadata, dict) and bool(metadata.get("cancel_requested_at"))

    def record_filled_without_allocation(
        self,
        position_id: str,
        *,
        exchange_order_id: str,
    ) -> tuple[LogicalPositionRecord | None, int, bool]:
        """Persist one observation of a filled order whose fills are unavailable."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logical_positions WHERE id = ? AND exchange_order_id = ?",
                (position_id, exchange_order_id),
            ).fetchone()
            if row is None:
                return None, 0, False
            position = LogicalPositionRecord.from_row(row)
            metadata = _json_loads(position.metadata_json, {})
            if not isinstance(metadata, dict):
                metadata = {}
            observation = metadata.get("filled_without_allocation")
            if not isinstance(observation, dict) or observation.get("order_id") != exchange_order_id:
                observation = {
                    "order_id": exchange_order_id,
                    "first_seen_at": datetime.now(timezone.utc).isoformat(),
                    "count": 0,
                }
            observation["count"] = int(observation.get("count") or 0) + 1
            observation["last_seen_at"] = datetime.now(timezone.utc).isoformat()
            metadata["filled_without_allocation"] = observation
            metadata["execution_status"] = "filled_awaiting_allocation"
            conn.execute(
                "UPDATE logical_positions SET metadata_json = ?, updated_at = ? "
                "WHERE id = ? AND exchange_order_id = ?",
                (
                    _json_dumps(metadata),
                    datetime.now(timezone.utc).isoformat(),
                    position_id,
                    exchange_order_id,
                ),
            )
        return self.get(position_id), int(observation["count"]), bool(
            observation.get("alerted_at")
        )

    def mark_filled_without_allocation_alerted(
        self,
        position_id: str,
        *,
        exchange_order_id: str,
    ) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM logical_positions "
                "WHERE id = ? AND exchange_order_id = ?",
                (position_id, exchange_order_id),
            ).fetchone()
            if row is None:
                return False
            metadata = _json_loads(row["metadata_json"], {})
            observation = (
                metadata.get("filled_without_allocation")
                if isinstance(metadata, dict)
                else None
            )
            if not isinstance(observation, dict) or observation.get("alerted_at"):
                return False
            observation["alerted_at"] = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "UPDATE logical_positions SET metadata_json = ?, updated_at = ? "
                "WHERE id = ? AND exchange_order_id = ?",
                (
                    _json_dumps(metadata),
                    datetime.now(timezone.utc).isoformat(),
                    position_id,
                    exchange_order_id,
                ),
            )
        return True

    def update_reconciliation(
        self,
        position_id: str,
        *,
        exchange_position_key: str,
        reconciliation: dict,
    ) -> LogicalPositionRecord | None:
        position = self.get(position_id)
        if position is None:
            return None
        position.exchange_position_key = exchange_position_key
        try:
            metadata = json.loads(position.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {"raw": position.metadata_json}
        metadata["reconciliation"] = reconciliation
        position.metadata_json = json.dumps(metadata)
        self.save(position)
        return position

    def applied_schema_versions(self) -> list[int]:
        with self._conn() as conn:
            return applied_schema_versions(conn, component=_SCHEMA_COMPONENT)

    def record_allocation(
        self,
        allocation: LogicalPositionAllocation,
        *,
        apply_to_position: bool = True,
    ) -> LogicalPositionRecord | None:
        with self._conn() as conn:
            position_row = conn.execute(
                "SELECT * FROM logical_positions WHERE id = ?",
                (allocation.position_id,),
            ).fetchone()
            if position_row is None:
                return None
            position = LogicalPositionRecord.from_row(position_row)
            existing_row = conn.execute(
                "SELECT * FROM logical_position_allocations WHERE id = ?",
                (allocation.id,),
            ).fetchone()
            if existing_row is not None:
                existing = LogicalPositionAllocation.from_row(existing_row)
                if not self._allocations_equivalent(existing, allocation):
                    raise AllocationConflictError(
                        f"Allocation id {allocation.id!r} already has different data"
                    )
                return position

            self._validate_allocation(position, allocation)
            conn.execute(
                """INSERT INTO logical_position_allocations
                   (id, position_id, action, quantity, price, fee,
                    exchange_order_id, reason, created_at, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    allocation.id,
                    allocation.position_id,
                    allocation.action,
                    allocation.quantity,
                    allocation.price,
                    allocation.fee,
                    allocation.exchange_order_id,
                    allocation.reason,
                    allocation.created_at,
                    allocation.metadata_json,
                ),
            )
            if apply_to_position:
                self._apply_allocation_fields(position, allocation)
                position.updated_at = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """UPDATE logical_positions SET
                       opened_quantity = ?, remaining_quantity = ?, entry_price = ?,
                       status = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        position.opened_quantity,
                        position.remaining_quantity,
                        position.entry_price,
                        position.status,
                        position.updated_at,
                        position.id,
                    ),
                )
        return position

    def get_allocation(self, allocation_id: str) -> LogicalPositionAllocation | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM logical_position_allocations WHERE id = ?",
                (allocation_id,),
            ).fetchone()
        return None if row is None else LogicalPositionAllocation.from_row(row)

    def list_allocations(self, position_id: str) -> list[LogicalPositionAllocation]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM logical_position_allocations
                   WHERE position_id = ?
                   ORDER BY created_at""",
                (position_id,),
            ).fetchall()
        return [LogicalPositionAllocation.from_row(row) for row in rows]

    def save_close_condition(self, condition: LogicalPositionCloseCondition) -> str | None:
        if self.get(condition.position_id) is None:
            return None
        condition.updated_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO logical_position_close_conditions
                   (id, position_id, purpose, expression_json, enabled,
                    metadata_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    position_id = excluded.position_id,
                    purpose = excluded.purpose,
                    expression_json = excluded.expression_json,
                    enabled = excluded.enabled,
                    metadata_json = excluded.metadata_json,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at""",
                (
                    condition.id,
                    condition.position_id,
                    condition.purpose,
                    condition.expression_json,
                    1 if condition.enabled else 0,
                    condition.metadata_json,
                    condition.created_at,
                    condition.updated_at,
                ),
            )
        return condition.id

    def create_close_condition(
        self,
        *,
        position_id: str,
        purpose: CloseConditionPurpose | str = "exit",
        expression: dict[str, Any] | None = None,
        enabled: bool = True,
        metadata: dict[str, Any] | None = None,
        id: str | None = None,
    ) -> LogicalPositionCloseCondition | None:
        condition = LogicalPositionCloseCondition(
            id=id,
            position_id=position_id,
            purpose=purpose,
            expression_json=_json_dumps(expression or {}),
            enabled=enabled,
            metadata_json=_json_dumps(metadata or {}),
        )
        saved_id = self.save_close_condition(condition)
        return None if saved_id is None else condition

    def get_close_condition(
        self,
        position_id: str,
        condition_id: str,
    ) -> LogicalPositionCloseCondition | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM logical_position_close_conditions
                   WHERE position_id = ? AND id = ?""",
                (position_id, condition_id),
            ).fetchone()
        return None if row is None else LogicalPositionCloseCondition.from_row(row)

    def list_close_conditions(
        self,
        position_id: str,
        *,
        enabled: bool | None = None,
    ) -> list[LogicalPositionCloseCondition]:
        query = "SELECT * FROM logical_position_close_conditions WHERE position_id = ?"
        params: list[Any] = [position_id]
        if enabled is not None:
            query += " AND enabled = ?"
            params.append(1 if enabled else 0)
        query += " ORDER BY created_at"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [LogicalPositionCloseCondition.from_row(row) for row in rows]

    def update_close_condition(
        self,
        position_id: str,
        condition_id: str,
        *,
        purpose: CloseConditionPurpose | str | None = None,
        expression: dict[str, Any] | None = None,
        enabled: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LogicalPositionCloseCondition | None:
        condition = self.get_close_condition(position_id, condition_id)
        if condition is None:
            return None
        if purpose is not None:
            condition.purpose = purpose
        if expression is not None:
            condition.expression_json = _json_dumps(expression)
        if enabled is not None:
            condition.enabled = enabled
        if metadata is not None:
            condition.metadata_json = _json_dumps(metadata)
        self.save_close_condition(condition)
        return self.get_close_condition(position_id, condition_id)

    def delete_close_condition(self, position_id: str, condition_id: str) -> bool:
        with self._conn() as conn:
            cursor = conn.execute(
                """DELETE FROM logical_position_close_conditions
                   WHERE position_id = ? AND id = ?""",
                (position_id, condition_id),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _allocations_equivalent(
        existing: LogicalPositionAllocation,
        candidate: LogicalPositionAllocation,
    ) -> bool:
        return (
            existing.position_id == candidate.position_id
            and existing.action == candidate.action
            and existing.quantity == candidate.quantity
            and existing.price == candidate.price
            and existing.fee == candidate.fee
            and existing.exchange_order_id == candidate.exchange_order_id
            and existing.reason == candidate.reason
            and _json_loads(existing.metadata_json, {})
            == _json_loads(candidate.metadata_json, {})
        )

    @staticmethod
    def _validate_allocation(
        position: LogicalPositionRecord,
        allocation: LogicalPositionAllocation,
    ) -> None:
        if allocation.quantity < 0:
            raise ValueError("Allocation quantity cannot be negative")
        if allocation.action in {"open", "reduce", "close"} and allocation.quantity <= 0:
            raise ValueError(f"{allocation.action} allocation quantity must be positive")
        if allocation.action == "open" and position.status not in {"planned", "pending_open", "open"}:
            raise ValueError(f"Cannot apply open allocation to {position.status} position")
        if allocation.action in {"reduce", "close"}:
            current = position.remaining_quantity
            if current is None:
                current = position.opened_quantity or 0.0
            if allocation.quantity > current + 1e-12:
                raise ValueError(
                    f"Allocation quantity {allocation.quantity} exceeds remaining quantity {current}"
                )

    @staticmethod
    def _apply_allocation_fields(
        position: LogicalPositionRecord,
        allocation: LogicalPositionAllocation,
    ) -> None:
        if allocation.action == "open":
            base = position.opened_quantity or 0.0
            total = round(base + allocation.quantity, 12)
            if allocation.price is not None and total > 0:
                position.entry_price = (
                    allocation.price
                    if base == 0
                    else round(
                        ((position.entry_price * base) + (allocation.price * allocation.quantity))
                        / total,
                        12,
                    )
                )
            position.opened_quantity = total
            if position.remaining_quantity is None:
                position.remaining_quantity = 0.0
            position.remaining_quantity = round(position.remaining_quantity + allocation.quantity, 12)
            if position.status in {"planned", "pending_open"}:
                position.status = "open"
        elif allocation.action in {"reduce", "close"}:
            current = position.remaining_quantity
            if current is None:
                current = position.opened_quantity
            if current is None:
                current = 0.0
            position.remaining_quantity = max(0.0, round(current - allocation.quantity, 12))
            if position.remaining_quantity == 0:
                position.status = "closed"
            elif allocation.action == "close":
                position.status = "closing"
            else:
                position.status = "open"
        elif allocation.action == "adjustment":
            position.remaining_quantity = max(0.0, round(allocation.quantity, 12))
            position.status = "closed" if position.remaining_quantity == 0 else "open"
