"""SQLite persistence for durable audit and decision events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator
from uuid import uuid4

from src.config.settings import settings
from src.daemon.events import RuntimeEvent
from src.trading.sqlite_schema import (
    applied_schema_versions,
    connect_database,
    configure_connection,
    initialize_schema,
    record_schema_version,
    sqlite_read_only,
)


_SCHEMA_COMPONENT = "audit_events"
_SCHEMA_VERSION = 2


_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    source      TEXT NOT NULL,
    position_id TEXT NOT NULL DEFAULT '',
    trade_id    TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_events_created_at
    ON audit_events(created_at);
CREATE INDEX IF NOT EXISTS idx_audit_events_type
    ON audit_events(type);
CREATE INDEX IF NOT EXISTS idx_audit_events_source
    ON audit_events(source);
CREATE INDEX IF NOT EXISTS idx_audit_events_position
    ON audit_events(position_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_trade
    ON audit_events(trade_id);
"""

_SCHEMA_V2_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_audit_events_strategy
    ON audit_events(strategy_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_correlation
    ON audit_events(correlation_id);
"""


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _json_loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return fallback


class AuditEventRecord:
    """Durable audit event for actions, decisions, and runtime evidence."""

    __slots__ = (
        "id",
        "type",
        "source",
        "strategy_id",
        "correlation_id",
        "position_id",
        "trade_id",
        "payload_json",
        "created_at",
    )

    def __init__(
        self,
        *,
        id: str | None = None,
        type: str,
        source: str,
        strategy_id: str = "",
        correlation_id: str = "",
        position_id: str = "",
        trade_id: str = "",
        payload_json: str = "{}",
        created_at: str = "",
    ) -> None:
        self.id = id or uuid4().hex
        self.type = type
        self.source = source
        self.strategy_id = strategy_id
        self.correlation_id = correlation_id
        self.position_id = position_id
        self.trade_id = trade_id
        self.payload_json = payload_json
        self.created_at = created_at or datetime.now(timezone.utc).isoformat()

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "AuditEventRecord":
        return cls(**{key: row[key] for key in row.keys()})

    @classmethod
    def from_runtime_event(cls, event: RuntimeEvent) -> "AuditEventRecord":
        payload = event.payload or {}
        return cls(
            id=event.id,
            type=event.type,
            source=event.source,
            strategy_id=str(payload.get("strategy_id") or ""),
            correlation_id=str(payload.get("correlation_id") or ""),
            position_id=str(payload.get("position_id") or ""),
            trade_id=str(payload.get("trade_id") or ""),
            payload_json=_json_dumps(payload),
            created_at=event.created_at.isoformat(),
        )

    @property
    def payload(self) -> dict[str, Any]:
        value = _json_loads(self.payload_json, {})
        return value if isinstance(value, dict) else {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "source": self.source,
            "strategy_id": self.strategy_id or None,
            "correlation_id": self.correlation_id or None,
            "position_id": self.position_id or None,
            "trade_id": self.trade_id or None,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class AuditEventStore:
    """SQLite-backed audit event persistence."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.MAYBECH_DB_PATH
        if not sqlite_read_only():
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
            if _SCHEMA_VERSION not in applied_schema_versions(
                conn, component=_SCHEMA_COMPONENT
            ):
                self._migrate_v2(conn)

    @staticmethod
    def _migrate_v2(conn: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(audit_events)").fetchall()
        }
        if "strategy_id" not in columns:
            conn.execute(
                "ALTER TABLE audit_events ADD COLUMN strategy_id TEXT NOT NULL DEFAULT ''"
            )
        if "correlation_id" not in columns:
            conn.execute(
                "ALTER TABLE audit_events ADD COLUMN correlation_id TEXT NOT NULL DEFAULT ''"
            )
        conn.executescript(_SCHEMA_V2_INDEXES)
        record_schema_version(conn, component=_SCHEMA_COMPONENT, version=2)

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = connect_database(self.db_path)
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

    @staticmethod
    def _save_on_connection(conn: sqlite3.Connection, event: AuditEventRecord) -> None:
        conn.execute(
                """INSERT INTO audit_events
                   (id, type, source, strategy_id, correlation_id, position_id,
                    trade_id, payload_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                    type = excluded.type,
                    source = excluded.source,
                    strategy_id = excluded.strategy_id,
                    correlation_id = excluded.correlation_id,
                    position_id = excluded.position_id,
                    trade_id = excluded.trade_id,
                    payload_json = excluded.payload_json,
                    created_at = excluded.created_at""",
                (
                    event.id,
                    event.type,
                    event.source,
                    event.strategy_id,
                    event.correlation_id,
                    event.position_id,
                    event.trade_id,
                    event.payload_json,
                    event.created_at,
                ),
            )

    def save(
        self,
        event: AuditEventRecord,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> str:
        if connection is not None:
            self._save_on_connection(connection, event)
        else:
            with self._conn() as conn:
                self._save_on_connection(conn, event)
        return event.id

    def create(
        self,
        *,
        type: str,
        source: str,
        payload: dict[str, Any] | None = None,
        id: str | None = None,
        created_at: str = "",
        connection: sqlite3.Connection | None = None,
    ) -> AuditEventRecord:
        payload = payload or {}
        event = AuditEventRecord(
            id=id,
            type=type,
            source=source,
            strategy_id=str(payload.get("strategy_id") or ""),
            correlation_id=str(payload.get("correlation_id") or ""),
            position_id=str(payload.get("position_id") or ""),
            trade_id=str(payload.get("trade_id") or ""),
            payload_json=_json_dumps(payload),
            created_at=created_at,
        )
        self.save(event, connection=connection)
        return event

    def save_runtime_event(self, event: RuntimeEvent) -> str:
        return self.save(AuditEventRecord.from_runtime_event(event))

    def list(
        self,
        *,
        limit: int = 100,
        event_type: str | None = None,
        source: str | None = None,
        strategy_id: str | None = None,
        correlation_id: str | None = None,
        position_id: str | None = None,
        trade_id: str | None = None,
        before: str | None = None,
    ) -> list[AuditEventRecord]:
        query = "SELECT * FROM audit_events WHERE 1=1"
        params: list[Any] = []
        if event_type:
            query += " AND type = ?"
            params.append(event_type)
        if source:
            query += " AND source = ?"
            params.append(source)
        if strategy_id:
            query += " AND strategy_id = ?"
            params.append(strategy_id)
        if correlation_id:
            query += " AND correlation_id = ?"
            params.append(correlation_id)
        if position_id:
            query += " AND position_id = ?"
            params.append(position_id)
        if trade_id:
            query += " AND trade_id = ?"
            params.append(trade_id)
        if before:
            query += " AND created_at < ?"
            params.append(before)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [AuditEventRecord.from_row(row) for row in rows]

    def list_strategy_decisions(
        self,
        *,
        strategy_id: str,
        limit: int = 100,
        allowed: bool | None = None,
        execution_status: str | None = None,
        before: str | None = None,
    ) -> list[AuditEventRecord]:
        query = (
            "SELECT * FROM audit_events "
            "WHERE type = 'strategy.action_decision' AND strategy_id = ?"
        )
        params: list[Any] = [strategy_id]
        if allowed is not None:
            query += " AND json_extract(payload_json, '$.allowed') = ?"
            params.append(int(allowed))
        if execution_status:
            query += " AND json_extract(payload_json, '$.execution_status') = ?"
            params.append(execution_status)
        if before:
            query += " AND created_at < ?"
            params.append(before)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [AuditEventRecord.from_row(row) for row in rows]
