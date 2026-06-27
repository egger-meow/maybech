import sqlite3

from src.trading.sqlite_schema import (
    applied_schema_versions,
    configure_connection,
    initialize_schema,
)


def test_configure_connection_enables_foreign_keys(tmp_path):
    conn = sqlite3.connect(tmp_path / "schema.db")
    try:
        configure_connection(conn)

        foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]

        assert conn.row_factory is sqlite3.Row
        assert foreign_keys == 1
    finally:
        conn.close()


def test_initialize_schema_records_component_version_idempotently(tmp_path):
    conn = sqlite3.connect(tmp_path / "schema.db")
    try:
        configure_connection(conn)
        initialize_schema(
            conn,
            schema_sql="CREATE TABLE IF NOT EXISTS example (id TEXT PRIMARY KEY);",
            component="example_store",
            version=1,
        )
        initialize_schema(
            conn,
            schema_sql="CREATE TABLE IF NOT EXISTS example (id TEXT PRIMARY KEY);",
            component="example_store",
            version=1,
        )

        assert applied_schema_versions(conn, component="example_store") == [1]
    finally:
        conn.close()
