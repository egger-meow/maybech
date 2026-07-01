"""SQLite persistence for durable audit and decision events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
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
_SCHEMA_VERSION = 4


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

_SCHEMA_V3_NOTIFICATIONS = """
CREATE TABLE IF NOT EXISTS notification_delivery_cursors (
    consumer         TEXT PRIMARY KEY,
    event_created_at TEXT NOT NULL,
    event_id         TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_delivery_acknowledgements (
    consumer        TEXT NOT NULL,
    event_id        TEXT NOT NULL,
    channel         TEXT NOT NULL,
    acknowledged_at TEXT NOT NULL,
    PRIMARY KEY (consumer, event_id, channel),
    FOREIGN KEY (event_id) REFERENCES audit_events(id) ON DELETE CASCADE
);
"""

_SCHEMA_V4_NOTIFICATION_HEALTH = """
CREATE TABLE IF NOT EXISTS notification_delivery_health (
    consumer             TEXT NOT NULL,
    channel              TEXT NOT NULL,
    last_event_id        TEXT NOT NULL DEFAULT '',
    last_attempt_at      TEXT NOT NULL DEFAULT '',
    last_success_at      TEXT NOT NULL DEFAULT '',
    last_failure_at      TEXT NOT NULL DEFAULT '',
    last_error           TEXT NOT NULL DEFAULT '',
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_retry_at        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (consumer, channel)
);
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
            if 2 not in applied_schema_versions(
                conn, component=_SCHEMA_COMPONENT
            ):
                self._migrate_v2(conn)
            versions = applied_schema_versions(conn, component=_SCHEMA_COMPONENT)
            if 3 not in versions:
                self._migrate_v3(conn)
            versions = applied_schema_versions(conn, component=_SCHEMA_COMPONENT)
            if 4 not in versions:
                self._migrate_v4(conn)

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

    @staticmethod
    def _migrate_v3(conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA_V3_NOTIFICATIONS)
        record_schema_version(conn, component=_SCHEMA_COMPONENT, version=3)

    @staticmethod
    def _migrate_v4(conn: sqlite3.Connection) -> None:
        conn.executescript(_SCHEMA_V4_NOTIFICATION_HEALTH)
        record_schema_version(conn, component=_SCHEMA_COMPONENT, version=4)

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

    def initialize_delivery_cursor(self, consumer: str) -> tuple[str, str] | None:
        """Create a first-run cursor at the newest event without overwriting one."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT event_created_at, event_id FROM notification_delivery_cursors "
                "WHERE consumer = ?",
                (consumer,),
            ).fetchone()
            if row is not None:
                return str(row["event_created_at"]), str(row["event_id"])
            latest = conn.execute(
                "SELECT created_at, id FROM audit_events "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if latest is None:
                return None
            cursor = (str(latest["created_at"]), str(latest["id"]))
            conn.execute(
                "INSERT INTO notification_delivery_cursors "
                "(consumer, event_created_at, event_id, updated_at) VALUES (?, ?, ?, ?)",
                (consumer, cursor[0], cursor[1], now),
            )
            return cursor

    def list_after_delivery_cursor(
        self,
        consumer: str,
        *,
        limit: int = 500,
    ) -> list[AuditEventRecord]:
        """Return events after the acknowledged cursor in deterministic order."""
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT event_created_at, event_id FROM notification_delivery_cursors "
                "WHERE consumer = ?",
                (consumer,),
            ).fetchone()
            if cursor is None:
                rows = conn.execute(
                    "SELECT * FROM audit_events ORDER BY created_at ASC, id ASC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_events "
                    "WHERE created_at > ? OR (created_at = ? AND id > ?) "
                    "ORDER BY created_at ASC, id ASC LIMIT ?",
                    (
                        cursor["event_created_at"],
                        cursor["event_created_at"],
                        cursor["event_id"],
                        limit,
                    ),
                ).fetchall()
        return [AuditEventRecord.from_row(row) for row in rows]

    def acknowledge_delivery_channel(
        self,
        consumer: str,
        event_id: str,
        channel: str,
    ) -> None:
        acknowledged_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO notification_delivery_acknowledgements "
                "(consumer, event_id, channel, acknowledged_at) VALUES (?, ?, ?, ?)",
                (consumer, event_id, channel, acknowledged_at),
            )

    def acknowledged_delivery_channels(
        self,
        consumer: str,
        event_id: str,
    ) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT channel FROM notification_delivery_acknowledgements "
                "WHERE consumer = ? AND event_id = ?",
                (consumer, event_id),
            ).fetchall()
        return {str(row["channel"]) for row in rows}

    def advance_delivery_cursor(
        self,
        consumer: str,
        event: AuditEventRecord,
    ) -> None:
        """Advance only after the caller has acknowledged every required channel."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO notification_delivery_cursors "
                "(consumer, event_created_at, event_id, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(consumer) DO UPDATE SET "
                "event_created_at = excluded.event_created_at, "
                "event_id = excluded.event_id, updated_at = excluded.updated_at",
                (consumer, event.created_at, event.id, now),
            )
            conn.execute(
                "DELETE FROM notification_delivery_acknowledgements "
                "WHERE consumer = ? AND event_id IN ("
                "SELECT id FROM audit_events WHERE created_at < ? "
                "OR (created_at = ? AND id <= ?))",
                (consumer, event.created_at, event.created_at, event.id),
            )

    def notification_acknowledgement_count(self, consumer: str) -> int:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM notification_delivery_acknowledgements "
                "WHERE consumer = ?",
                (consumer,),
            ).fetchone()
        return int(row["count"] if row is not None else 0)

    def pending_delivery_count(self, consumer: str) -> int:
        with self._conn() as conn:
            cursor = conn.execute(
                "SELECT event_created_at, event_id FROM notification_delivery_cursors "
                "WHERE consumer = ?",
                (consumer,),
            ).fetchone()
            if cursor is None:
                row = conn.execute("SELECT COUNT(*) AS count FROM audit_events").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM audit_events "
                    "WHERE created_at > ? OR (created_at = ? AND id > ?)",
                    (
                        cursor["event_created_at"],
                        cursor["event_created_at"],
                        cursor["event_id"],
                    ),
                ).fetchone()
        return int(row["count"] if row is not None else 0)

    def notification_delivery_health(self, consumer: str) -> dict[str, dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM notification_delivery_health WHERE consumer = ?",
                (consumer,),
            ).fetchall()
        return {str(row["channel"]): dict(row) for row in rows}

    def notification_channel_retry_ready(
        self,
        consumer: str,
        channel: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        current = now or datetime.now(timezone.utc)
        health = self.notification_delivery_health(consumer).get(channel)
        if not health or not health.get("next_retry_at"):
            return True
        try:
            return current >= datetime.fromisoformat(str(health["next_retry_at"]))
        except ValueError:
            return True

    def record_notification_delivery_attempt(
        self,
        consumer: str,
        channel: str,
        *,
        event_id: str,
        succeeded: bool,
        error: str = "",
        retry_base_seconds: float = 5,
        retry_max_seconds: float = 900,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._conn() as conn:
            previous = conn.execute(
                "SELECT * FROM notification_delivery_health "
                "WHERE consumer = ? AND channel = ?",
                (consumer, channel),
            ).fetchone()
            failures = 0 if succeeded else int(previous["consecutive_failures"] if previous else 0) + 1
            delay = min(retry_max_seconds, retry_base_seconds * (2 ** max(0, failures - 1)))
            next_retry_at = "" if succeeded else (now + timedelta(seconds=delay)).isoformat()
            conn.execute(
                "INSERT INTO notification_delivery_health "
                "(consumer, channel, last_event_id, last_attempt_at, last_success_at, "
                "last_failure_at, last_error, consecutive_failures, next_retry_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(consumer, channel) DO UPDATE SET "
                "last_event_id = excluded.last_event_id, "
                "last_attempt_at = excluded.last_attempt_at, "
                "last_success_at = CASE WHEN excluded.last_success_at != '' "
                "THEN excluded.last_success_at ELSE notification_delivery_health.last_success_at END, "
                "last_failure_at = CASE WHEN excluded.last_failure_at != '' "
                "THEN excluded.last_failure_at ELSE notification_delivery_health.last_failure_at END, "
                "last_error = excluded.last_error, "
                "consecutive_failures = excluded.consecutive_failures, "
                "next_retry_at = excluded.next_retry_at",
                (
                    consumer,
                    channel,
                    event_id,
                    now.isoformat(),
                    now.isoformat() if succeeded else "",
                    "" if succeeded else now.isoformat(),
                    "" if succeeded else (error or "transport returned failure")[:256],
                    failures,
                    next_retry_at,
                ),
            )
        return self.notification_delivery_health(consumer)[channel]
