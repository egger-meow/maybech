"""Durable pagination checkpoints for authenticated execution streams."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

from src.config.settings import settings
from src.trading.sqlite_schema import (
    applied_schema_versions,
    configure_connection,
    initialize_schema,
)


_SCHEMA_COMPONENT = "execution_cursors"
_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_cursors (
    stream_id                  TEXT PRIMARY KEY,
    high_water_id              TEXT NOT NULL DEFAULT '',
    pending_high_water_id      TEXT NOT NULL DEFAULT '',
    next_after_id              TEXT NOT NULL DEFAULT '',
    updated_at                 TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class ExecutionCursor:
    stream_id: str
    high_water_id: str = ""
    pending_high_water_id: str = ""
    next_after_id: str = ""
    updated_at: str = ""

    @property
    def in_progress(self) -> bool:
        return bool(self.pending_high_water_id or self.next_after_id)


class ExecutionCursorStore:
    """Persist page progress without committing a partial catch-up cycle."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or settings.MAYBECH_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            initialize_schema(
                conn,
                schema_sql=_SCHEMA,
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

    def get(self, stream_id: str) -> ExecutionCursor:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM execution_cursors WHERE stream_id = ?",
                (stream_id,),
            ).fetchone()
        if row is None:
            return ExecutionCursor(stream_id=stream_id)
        return ExecutionCursor(**{key: row[key] for key in row.keys()})

    def checkpoint(
        self,
        stream_id: str,
        *,
        pending_high_water_id: str,
        next_after_id: str,
    ) -> ExecutionCursor:
        current = self.get(stream_id)
        return self._save(
            ExecutionCursor(
                stream_id=stream_id,
                high_water_id=current.high_water_id,
                pending_high_water_id=pending_high_water_id,
                next_after_id=next_after_id,
            )
        )

    def complete(self, stream_id: str, *, high_water_id: str) -> ExecutionCursor:
        return self._save(
            ExecutionCursor(
                stream_id=stream_id,
                high_water_id=high_water_id,
            )
        )

    def _save(self, cursor: ExecutionCursor) -> ExecutionCursor:
        updated_at = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO execution_cursors
                   (stream_id, high_water_id, pending_high_water_id,
                    next_after_id, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(stream_id) DO UPDATE SET
                    high_water_id = excluded.high_water_id,
                    pending_high_water_id = excluded.pending_high_water_id,
                    next_after_id = excluded.next_after_id,
                    updated_at = excluded.updated_at""",
                (
                    cursor.stream_id,
                    cursor.high_water_id,
                    cursor.pending_high_water_id,
                    cursor.next_after_id,
                    updated_at,
                ),
            )
        return ExecutionCursor(
            stream_id=cursor.stream_id,
            high_water_id=cursor.high_water_id,
            pending_high_water_id=cursor.pending_high_water_id,
            next_after_id=cursor.next_after_id,
            updated_at=updated_at,
        )
