"""Shared SQLite schema-version helpers for trading persistence stores."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    component  TEXT NOT NULL,
    version    INTEGER NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (component, version)
);
"""


def configure_connection(conn: sqlite3.Connection) -> None:
    """Apply the connection settings every Maybech runtime store expects."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")


def initialize_schema(
    conn: sqlite3.Connection,
    *,
    schema_sql: str,
    component: str,
    version: int,
) -> None:
    """Create schema objects and record the active schema version."""
    conn.executescript(MIGRATION_TABLE_SQL)
    conn.executescript(schema_sql)
    record_schema_version(conn, component=component, version=version)


def record_schema_version(
    conn: sqlite3.Connection,
    *,
    component: str,
    version: int,
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO schema_migrations
           (component, version, applied_at)
           VALUES (?, ?, ?)""",
        (component, version, datetime.now(timezone.utc).isoformat()),
    )


def applied_schema_versions(conn: sqlite3.Connection, *, component: str) -> list[int]:
    rows = conn.execute(
        """SELECT version FROM schema_migrations
           WHERE component = ?
           ORDER BY version""",
        (component,),
    ).fetchall()
    return [int(row["version"]) for row in rows]
