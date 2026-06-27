import sqlite3

from src.daemon.events import EventBus
from src.trading.audit_event_store import AuditEventStore
from src.trading.sqlite_schema import configure_connection, initialize_schema


def test_audit_event_store_records_schema_version(tmp_path):
    store = AuditEventStore(str(tmp_path / "audit.db"))

    assert store.applied_schema_versions() == [1, 2]


def test_audit_event_store_migrates_version_one_database(tmp_path):
    db_path = str(tmp_path / "audit.db")
    conn = sqlite3.connect(db_path)
    try:
        configure_connection(conn)
        initialize_schema(
            conn,
            schema_sql="""
                CREATE TABLE audit_events (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    position_id TEXT NOT NULL DEFAULT '',
                    trade_id TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
            """,
            component="audit_events",
            version=1,
        )
        conn.commit()
    finally:
        conn.close()

    store = AuditEventStore(db_path)

    conn = sqlite3.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_events)")}
    finally:
        conn.close()
    assert {"strategy_id", "correlation_id"} <= columns
    assert store.applied_schema_versions() == [1, 2]


def test_audit_event_store_saves_and_filters_events(tmp_path):
    store = AuditEventStore(str(tmp_path / "audit.db"))
    store.create(
        type="position.close_condition_evaluated",
        source="position_manager",
        payload={
            "position_id": "unit-a",
            "trade_id": "trade-a",
            "matched": True,
        },
    )
    store.create(
        type="position.closed",
        source="position_manager",
        payload={
            "position_id": "unit-b",
            "trade_id": "trade-b",
            "matched": True,
        },
    )

    by_type = store.list(event_type="position.closed")
    by_position = store.list(position_id="unit-a")
    by_trade = store.list(trade_id="trade-b")

    assert len(by_type) == 1
    assert by_type[0].type == "position.closed"
    assert by_position[0].payload["matched"] is True
    assert by_trade[0].position_id == "unit-b"


def test_audit_event_store_saves_runtime_event(tmp_path):
    store = AuditEventStore(str(tmp_path / "audit.db"))
    event = EventBus().publish(
        "position.close_blocked",
        "position_manager",
        {"position_id": "unit-a", "reason": "live blocked"},
    )

    store.save_runtime_event(event)

    persisted = store.list(position_id="unit-a")
    assert len(persisted) == 1
    assert persisted[0].id == event.id
    assert persisted[0].payload["reason"] == "live blocked"


def test_audit_event_store_filters_strategy_decisions_and_correlation(tmp_path):
    store = AuditEventStore(str(tmp_path / "audit.db"))
    store.create(
        type="strategy.action_decision",
        source="strategy",
        payload={
            "strategy_id": "momentum_swap",
            "correlation_id": "decision-a",
            "allowed": True,
            "execution_status": "simulated",
        },
    )
    store.create(
        type="strategy.action_decision",
        source="strategy",
        payload={
            "strategy_id": "momentum_swap",
            "correlation_id": "decision-b",
            "allowed": False,
            "execution_status": "blocked",
        },
    )

    allowed = store.list_strategy_decisions(
        strategy_id="momentum_swap",
        allowed=True,
    )
    blocked = store.list_strategy_decisions(
        strategy_id="momentum_swap",
        execution_status="blocked",
    )
    correlated = store.list(correlation_id="decision-a")

    assert [event.correlation_id for event in allowed] == ["decision-a"]
    assert [event.correlation_id for event in blocked] == ["decision-b"]
    assert correlated[0].strategy_id == "momentum_swap"
