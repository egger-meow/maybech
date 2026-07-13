"""Shared SQLite schema-version helpers for trading persistence stores."""

from __future__ import annotations

import sqlite3
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class UnsupportedSchemaError(RuntimeError):
    """The database was written by a newer Maybech build than this one."""


class DatabaseHealthError(RuntimeError):
    """The database file exists but cannot be trusted (corrupt or foreign)."""


_SQLITE_HEADER = b"SQLite format 3\x00"


_READ_ONLY_CONNECTIONS: ContextVar[bool] = ContextVar(
    "maybech_sqlite_read_only",
    default=False,
)


MIGRATION_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    component  TEXT NOT NULL,
    version    INTEGER NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (component, version)
);
"""


def set_sqlite_read_only(enabled: bool) -> Token[bool]:
    return _READ_ONLY_CONNECTIONS.set(enabled)


def reset_sqlite_read_only(token: Token[bool]) -> None:
    _READ_ONLY_CONNECTIONS.reset(token)


def sqlite_read_only() -> bool:
    return _READ_ONLY_CONNECTIONS.get()


def connect_database(db_path: str) -> sqlite3.Connection:
    if not sqlite_read_only():
        return sqlite3.connect(db_path)
    uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def configure_connection(conn: sqlite3.Connection) -> None:
    """Apply the connection settings every Maybech runtime store expects."""
    conn.row_factory = sqlite3.Row
    if sqlite_read_only():
        conn.execute("PRAGMA query_only=ON")
    else:
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


def assert_supported_schema(
    conn: sqlite3.Connection,
    *,
    component: str,
    max_supported: int,
) -> None:
    """Refuse to touch a component whose schema is newer than this build.

    A database restored from another machine or opened by an older checkout
    may carry migration versions this code has never heard of. Proceeding
    would run this build's DDL against a shape it does not understand, so the
    only safe answer is to stop before any store mutates anything. Call this
    before applying any schema SQL for the component.
    """
    conn.executescript(MIGRATION_TABLE_SQL)
    versions = applied_schema_versions(conn, component=component)
    newer = [version for version in versions if version > max_supported]
    if newer:
        raise UnsupportedSchemaError(
            f"database component {component!r} is at schema version {max(newer)}, "
            f"but this build supports at most version {max_supported}; "
            "upgrade Maybech (or restore the database backup that matches this "
            "build) instead of letting older code modify a newer database"
        )


def check_database_file(db_path: str) -> dict[str, Any]:
    """Classify the database file before any store opens it.

    Returns a status dict and never creates or deletes the file: ``missing``
    / ``empty`` mean SQLite will start a fresh database (callers should log
    that loudly so an unexpectedly vanished database is visible), ``ok``
    means the header and ``PRAGMA quick_check`` both pass. Anything else — a
    non-SQLite file at the path, or a corrupt database — raises
    :class:`DatabaseHealthError` so startup fails closed; the operator must
    restore a backup or move the file aside rather than have Maybech
    silently overwrite prior state. The check opens the database normally
    (not read-only) on purpose: a crash can leave a WAL journal behind, and
    only a writable connection can run SQLite's standard WAL recovery —
    read-only would misreport that healthy database as broken.
    """
    path = Path(db_path)
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    size = path.stat().st_size
    if size == 0:
        return {"status": "empty", "path": str(path)}
    with open(path, "rb") as handle:
        header = handle.read(len(_SQLITE_HEADER))
    if header != _SQLITE_HEADER:
        raise DatabaseHealthError(
            f"{path} exists but is not a SQLite database; Maybech will not "
            "overwrite it — move the file aside or restore a database backup"
        )
    try:
        conn = sqlite3.connect(str(path))
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        raise DatabaseHealthError(
            f"{path} failed the SQLite integrity check ({exc}); restore the "
            "database from a backup — Maybech will not modify a corrupt file"
        ) from exc
    problems = [str(row[0]) for row in rows if str(row[0]).lower() != "ok"]
    if problems:
        raise DatabaseHealthError(
            f"{path} failed the SQLite integrity check "
            f"({'; '.join(problems[:3])}); restore the database from a backup "
            "— Maybech will not modify a corrupt file"
        )
    return {"status": "ok", "path": str(path), "size_bytes": size}
