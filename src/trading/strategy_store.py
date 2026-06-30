"""SQLite persistence for strategy definitions and signal expressions."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from uuid import uuid4

from src.config.settings import settings
from src.trading.sqlite_schema import (
    applied_schema_versions,
    configure_connection,
    initialize_schema,
    record_schema_version,
)


_SCHEMA_COMPONENT = "strategies"
_SCHEMA_VERSION = 3


_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    kind                    TEXT NOT NULL DEFAULT 'signal',
    enabled                 INTEGER NOT NULL DEFAULT 0,
    target_instruments_json TEXT NOT NULL DEFAULT '[]',
    entry_signal_json       TEXT NOT NULL DEFAULT '{}',
    default_rules_json      TEXT NOT NULL DEFAULT '{}',
    metadata_json           TEXT NOT NULL DEFAULT '{}',
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_expressions (
    id              TEXT PRIMARY KEY,
    strategy_id     TEXT NOT NULL,
    purpose         TEXT NOT NULL DEFAULT 'entry',
    expression_json TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategies(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_strategies_enabled ON strategies(enabled);
CREATE INDEX IF NOT EXISTS idx_signal_expressions_strategy
    ON signal_expressions(strategy_id);
"""

_SCHEMA_V2 = """
CREATE TABLE IF NOT EXISTS strategy_evaluation_state (
    strategy_id TEXT NOT NULL,
    inst_id     TEXT NOT NULL,
    matched     INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (strategy_id, inst_id),
    FOREIGN KEY (strategy_id) REFERENCES strategies(id) ON DELETE CASCADE
);
"""

_SCHEMA_V3 = """
DELETE FROM strategies
WHERE kind = 'momentum'
   OR json_extract(entry_signal_json, '$.type') = 'volume_price_gap';
"""


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _json_loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return fallback


class StrategyRecord:
    """Persistent product-facing strategy definition."""

    __slots__ = (
        "id",
        "name",
        "kind",
        "enabled",
        "target_instruments_json",
        "entry_signal_json",
        "default_rules_json",
        "metadata_json",
        "created_at",
        "updated_at",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        name: str,
        kind: str = "signal",
        enabled: bool = False,
        target_instruments_json: str = "[]",
        entry_signal_json: str = "{}",
        default_rules_json: str = "{}",
        metadata_json: str = "{}",
        created_at: str = "",
        updated_at: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.id = id or uuid4().hex[:12]
        self.name = name
        self.kind = kind
        self.enabled = enabled
        self.target_instruments_json = target_instruments_json
        self.entry_signal_json = entry_signal_json
        self.default_rules_json = default_rules_json
        self.metadata_json = metadata_json
        self.created_at = created_at or now
        self.updated_at = updated_at or now

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "StrategyRecord":
        data = {key: row[key] for key in row.keys()}
        data["enabled"] = bool(data["enabled"])
        return cls(**data)

    def to_dict(self) -> dict:
        return {attr: getattr(self, attr) for attr in self.__slots__}

    @property
    def target_instruments(self) -> list[str]:
        value = _json_loads(self.target_instruments_json, [])
        return value if isinstance(value, list) else []

    @property
    def entry_signal(self) -> dict[str, Any]:
        value = _json_loads(self.entry_signal_json, {})
        return value if isinstance(value, dict) else {}

    @property
    def default_rules(self) -> dict[str, Any]:
        value = _json_loads(self.default_rules_json, {})
        return value if isinstance(value, dict) else {}

    @property
    def metadata(self) -> dict[str, Any]:
        value = _json_loads(self.metadata_json, {})
        return value if isinstance(value, dict) else {}


class SignalExpressionRecord:
    """Reusable signal expression owned by a strategy."""

    __slots__ = (
        "id",
        "strategy_id",
        "purpose",
        "expression_json",
        "created_at",
        "updated_at",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        strategy_id: str,
        purpose: str = "entry",
        expression_json: str = "{}",
        created_at: str = "",
        updated_at: str = "",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.id = id or uuid4().hex[:12]
        self.strategy_id = strategy_id
        self.purpose = purpose
        self.expression_json = expression_json
        self.created_at = created_at or now
        self.updated_at = updated_at or now

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SignalExpressionRecord":
        return cls(**{key: row[key] for key in row.keys()})

    @property
    def expression(self) -> dict[str, Any]:
        value = _json_loads(self.expression_json, {})
        return value if isinstance(value, dict) else {}


class StrategyStore:
    """SQLite-backed strategy and signal-expression persistence."""

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
                version=1,
            )
            versions = applied_schema_versions(conn, component=_SCHEMA_COMPONENT)
            if 2 not in versions:
                conn.executescript(_SCHEMA_V2)
                record_schema_version(
                    conn,
                    component=_SCHEMA_COMPONENT,
                    version=2,
                )
            if _SCHEMA_VERSION not in versions:
                conn.executescript(_SCHEMA_V3)
                record_schema_version(
                    conn,
                    component=_SCHEMA_COMPONENT,
                    version=_SCHEMA_VERSION,
                )

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

    def applied_schema_versions(self) -> list[int]:
        with self._conn() as conn:
            return applied_schema_versions(conn, component=_SCHEMA_COMPONENT)

    def record_evaluation(self, strategy_id: str, inst_id: str, *, matched: bool) -> bool:
        """Persist match state and return true only for a false-to-true edge."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT matched FROM strategy_evaluation_state
                   WHERE strategy_id = ? AND inst_id = ?""",
                (strategy_id, inst_id),
            ).fetchone()
            previous = bool(row["matched"]) if row is not None else False
            conn.execute(
                """INSERT INTO strategy_evaluation_state
                   (strategy_id, inst_id, matched, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(strategy_id, inst_id) DO UPDATE SET
                    matched = excluded.matched,
                    updated_at = excluded.updated_at""",
                (strategy_id, inst_id, 1 if matched else 0, now),
            )
        return matched and not previous

    def save(self, strategy: StrategyRecord) -> str:
        strategy.updated_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO strategies
                   (id, name, kind, enabled, target_instruments_json,
                    entry_signal_json, default_rules_json, metadata_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    kind = excluded.kind,
                    enabled = excluded.enabled,
                    target_instruments_json = excluded.target_instruments_json,
                    entry_signal_json = excluded.entry_signal_json,
                    default_rules_json = excluded.default_rules_json,
                    metadata_json = excluded.metadata_json,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at""",
                (
                    strategy.id,
                    strategy.name,
                    strategy.kind,
                    1 if strategy.enabled else 0,
                    strategy.target_instruments_json,
                    strategy.entry_signal_json,
                    strategy.default_rules_json,
                    strategy.metadata_json,
                    strategy.created_at,
                    strategy.updated_at,
                ),
            )
        return strategy.id

    def create(
        self,
        *,
        id: str | None = None,
        name: str,
        kind: str = "signal",
        enabled: bool = False,
        target_instruments: list[str] | None = None,
        entry_signal: dict[str, Any] | None = None,
        default_rules: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StrategyRecord:
        strategy = StrategyRecord(
            id=id,
            name=name,
            kind=kind,
            enabled=enabled,
            target_instruments_json=_json_dumps(target_instruments or []),
            entry_signal_json=_json_dumps(entry_signal or {}),
            default_rules_json=_json_dumps(default_rules or {}),
            metadata_json=_json_dumps(metadata or {}),
        )
        self.save(strategy)
        return strategy

    def get(self, strategy_id: str) -> StrategyRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM strategies WHERE id = ?",
                (strategy_id,),
            ).fetchone()
        return None if row is None else StrategyRecord.from_row(row)

    def list(self, *, enabled: bool | None = None) -> list[StrategyRecord]:
        query = "SELECT * FROM strategies WHERE 1=1"
        params: list[Any] = []
        if enabled is not None:
            query += " AND enabled = ?"
            params.append(1 if enabled else 0)
        query += " ORDER BY created_at"
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [StrategyRecord.from_row(row) for row in rows]

    def update(
        self,
        strategy_id: str,
        *,
        name: str | None = None,
        kind: str | None = None,
        enabled: bool | None = None,
        target_instruments: list[str] | None = None,
        entry_signal: dict[str, Any] | None = None,
        default_rules: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StrategyRecord | None:
        strategy = self.get(strategy_id)
        if strategy is None:
            return None
        if name is not None:
            strategy.name = name
        if kind is not None:
            strategy.kind = kind
        if enabled is not None:
            strategy.enabled = enabled
        if target_instruments is not None:
            strategy.target_instruments_json = _json_dumps(target_instruments)
        if entry_signal is not None:
            strategy.entry_signal_json = _json_dumps(entry_signal)
        if default_rules is not None:
            strategy.default_rules_json = _json_dumps(default_rules)
        if metadata is not None:
            strategy.metadata_json = _json_dumps(metadata)
        self.save(strategy)
        return self.get(strategy_id)

    def ensure(
        self,
        *,
        id: str,
        name: str,
        kind: str,
        enabled: bool,
        target_instruments: list[str],
        entry_signal: dict[str, Any],
        default_rules: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> StrategyRecord:
        existing = self.get(id)
        if existing is not None:
            return existing
        return self.create(
            id=id,
            name=name,
            kind=kind,
            enabled=enabled,
            target_instruments=target_instruments,
            entry_signal=entry_signal,
            default_rules=default_rules,
            metadata=metadata,
        )

    def save_signal_expression(self, expression: SignalExpressionRecord) -> str:
        expression.updated_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO signal_expressions
                   (id, strategy_id, purpose, expression_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    strategy_id = excluded.strategy_id,
                    purpose = excluded.purpose,
                    expression_json = excluded.expression_json,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at""",
                (
                    expression.id,
                    expression.strategy_id,
                    expression.purpose,
                    expression.expression_json,
                    expression.created_at,
                    expression.updated_at,
                ),
            )
        return expression.id

    def create_signal_expression(
        self,
        *,
        strategy_id: str,
        purpose: str = "entry",
        expression: dict[str, Any] | None = None,
        id: str | None = None,
    ) -> SignalExpressionRecord | None:
        if self.get(strategy_id) is None:
            return None
        record = SignalExpressionRecord(
            id=id,
            strategy_id=strategy_id,
            purpose=purpose,
            expression_json=_json_dumps(expression or {}),
        )
        self.save_signal_expression(record)
        return record

    def list_signal_expressions(self, strategy_id: str) -> list[SignalExpressionRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM signal_expressions
                   WHERE strategy_id = ?
                   ORDER BY created_at""",
                (strategy_id,),
            ).fetchall()
        return [SignalExpressionRecord.from_row(row) for row in rows]

    def get_signal_expression(
        self,
        strategy_id: str,
        expression_id: str,
    ) -> SignalExpressionRecord | None:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM signal_expressions
                   WHERE strategy_id = ? AND id = ?""",
                (strategy_id, expression_id),
            ).fetchone()
        return None if row is None else SignalExpressionRecord.from_row(row)

    def update_signal_expression(
        self,
        strategy_id: str,
        expression_id: str,
        *,
        purpose: str | None = None,
        expression: dict[str, Any] | None = None,
    ) -> SignalExpressionRecord | None:
        record = self.get_signal_expression(strategy_id, expression_id)
        if record is None:
            return None
        if purpose is not None:
            record.purpose = purpose
        if expression is not None:
            record.expression_json = _json_dumps(expression)
        self.save_signal_expression(record)
        return self.get_signal_expression(strategy_id, expression_id)

    def delete_signal_expression(self, strategy_id: str, expression_id: str) -> bool:
        with self._conn() as conn:
            result = conn.execute(
                """DELETE FROM signal_expressions
                   WHERE strategy_id = ? AND id = ?""",
                (strategy_id, expression_id),
            )
        return result.rowcount > 0

    def delete(self, strategy_id: str) -> bool:
        with self._conn() as conn:
            result = conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
        return result.rowcount > 0
