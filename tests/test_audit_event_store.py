import sqlite3

from src.daemon.events import EventBus
from src.trading.audit_event_store import AuditEventStore
from src.trading.sqlite_schema import configure_connection, initialize_schema


def test_audit_event_store_records_schema_version(tmp_path):
    store = AuditEventStore(str(tmp_path / "audit.db"))

    assert store.applied_schema_versions() == [1, 2, 3, 4, 5]


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
    assert store.applied_schema_versions() == [1, 2, 3, 4, 5]


def test_v5_migration_backfills_existing_rejection_without_renotifying(tmp_path):
    db_path = str(tmp_path / "audit.db")
    store = AuditEventStore(db_path)
    store.create(
        id="fill-rejection:bill-existing",
        type="execution.fill_rejected",
        source="execution_fills",
        payload={
            "category": "rejected",
            "bill_id": "bill-existing",
            "fill_id": "fill-existing",
            "error": "take_profit price must be above entry for long",
        },
    )
    with store._conn() as conn:
        conn.execute(
            "DELETE FROM schema_migrations WHERE component = ? AND version = 5",
            ("audit_events",),
        )
        conn.execute("DROP TABLE execution_fill_quarantine")

    migrated = AuditEventStore(db_path)
    quarantine, created = migrated.quarantine_fill_rejection(
        raw_fill={"billId": "bill-existing", "tradeId": "fill-existing"},
        error="take_profit price must be above entry for long",
        category="rejected",
        source="execution_fills",
    )

    assert created is False
    assert quarantine["occurrences"] == 2
    assert len(migrated.list(event_type="execution.fill_rejected")) == 1


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


def test_fill_rejection_quarantine_is_terminal_per_bill_and_error_signature(tmp_path):
    store = AuditEventStore(str(tmp_path / "audit.db"))
    raw_fill = {
        "billId": "bill-a",
        "tradeId": "fill-a",
        "ordId": "order-a",
        "instId": "ETH-USDT-SWAP",
    }

    first, first_created = store.quarantine_fill_rejection(
        raw_fill=raw_fill,
        error="take_profit price must be above entry for long",
        category="rejected",
        source="execution_fills",
    )
    repeated, repeated_created = AuditEventStore(store.db_path).quarantine_fill_rejection(
        raw_fill=raw_fill,
        error="take_profit price must be above entry for long",
        category="rejected",
        source="execution_fills",
    )
    changed, changed_created = store.quarantine_fill_rejection(
        raw_fill=raw_fill,
        error="stop_loss price must be below entry for long",
        category="rejected",
        source="execution_fills",
    )

    assert first_created is True
    assert repeated_created is False
    assert repeated["occurrences"] == 2
    assert repeated["first_seen_at"] == first["first_seen_at"]
    assert changed_created is True
    assert changed["error_signature"] != first["error_signature"]
    assert len(store.list_fill_quarantine()) == 2
    assert len(store.list(event_type="execution.fill_rejected")) == 2


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
            "strategy_id": "strategy-a",
            "correlation_id": "decision-a",
            "allowed": True,
            "execution_status": "simulated",
        },
    )
    store.create(
        type="strategy.action_decision",
        source="strategy",
        payload={
            "strategy_id": "strategy-a",
            "correlation_id": "decision-b",
            "allowed": False,
            "execution_status": "blocked",
        },
    )

    allowed = store.list_strategy_decisions(
        strategy_id="strategy-a",
        allowed=True,
    )
    blocked = store.list_strategy_decisions(
        strategy_id="strategy-a",
        execution_status="blocked",
    )
    correlated = store.list(correlation_id="decision-a")

    assert [event.correlation_id for event in allowed] == ["decision-a"]
    assert [event.correlation_id for event in blocked] == ["decision-b"]
    assert correlated[0].strategy_id == "strategy-a"
