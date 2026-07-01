from src.trading.strategy_store import PendingStrategyExecution, StrategyRecord, StrategyStore


def test_strategy_store_records_schema_version(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))

    assert store.applied_schema_versions() == [1, 2, 3, 4]


def test_strategy_store_creates_updates_and_lists_records(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    created = store.create(
        id="breakout",
        name="Breakout",
        kind="signal",
        enabled=True,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "price_above", "symbol": "self", "value": 10},
        default_rules={"close_conditions": []},
    )

    updated = store.update(
        created.id,
        enabled=False,
        target_instruments=["ETH-USDT-SWAP", "SOL-USDT-SWAP"],
    )

    assert updated.enabled is False
    assert updated.target_instruments == ["ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    assert updated.entry_signal["value"] == 10
    assert store.list()[0].id == "breakout"


def test_strategy_store_ensure_does_not_overwrite_existing_record(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(
        id="custom",
        name="Custom Strategy",
        enabled=False,
        target_instruments=["BTC-USDT-SWAP"],
    )

    existing = store.ensure(
        id="custom",
        name="Runtime Default",
        kind="signal",
        enabled=True,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "runtime"},
        default_rules={},
    )

    assert existing.name == "Custom Strategy"
    assert existing.enabled is False
    assert existing.target_instruments == ["BTC-USDT-SWAP"]


def test_strategy_store_records_only_false_to_true_edges(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(id="breakout", name="Breakout")

    assert store.record_evaluation("breakout", "ETH-USDT-SWAP", matched=True) is True
    assert store.record_evaluation("breakout", "ETH-USDT-SWAP", matched=True) is False
    assert store.record_evaluation("breakout", "ETH-USDT-SWAP", matched=False) is False
    assert store.record_evaluation("breakout", "ETH-USDT-SWAP", matched=True) is True


def test_strategy_store_v3_migration_removes_legacy_momentum_records(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(
        id="legacy-momentum",
        name="Legacy Momentum",
        kind="momentum",
        enabled=True,
        entry_signal={"type": "volume_price_gap"},
    )
    with store._conn() as conn:
        conn.execute(
            "DELETE FROM schema_migrations WHERE component = 'strategies' AND version = 3"
        )

    migrated = StrategyStore(store.db_path)

    assert migrated.get("legacy-momentum") is None
    assert migrated.applied_schema_versions() == [1, 2, 3, 4]


def test_strategy_store_signal_expressions_follow_parent_strategy(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.save(
        StrategyRecord(
            id="strategy-a",
            name="Strategy A",
            target_instruments_json='["ETH-USDT-SWAP"]',
            entry_signal_json='{"type":"price_above"}',
        )
    )

    created = store.create_signal_expression(
        strategy_id="strategy-a",
        purpose="entry",
        expression={"op": "and", "conditions": [{"type": "price_above", "value": 3000}]},
    )

    expressions = store.list_signal_expressions("strategy-a")
    assert created is not None
    assert expressions[0].strategy_id == "strategy-a"
    assert expressions[0].expression["op"] == "and"


def test_strategy_store_rejects_signal_expression_for_missing_strategy(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))

    created = store.create_signal_expression(
        strategy_id="missing",
        expression={"type": "price_above"},
    )

    assert created is None


def test_strategy_store_updates_and_deletes_signal_expressions(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(id="strategy-a", name="Strategy A")
    created = store.create_signal_expression(
        strategy_id="strategy-a",
        purpose="entry",
        expression={"type": "price_above", "symbol": "self", "value": 100},
    )
    assert created is not None

    updated = store.update_signal_expression(
        "strategy-a",
        created.id,
        purpose="filter",
        expression={"type": "price_below", "symbol": "self", "value": 200},
    )

    assert updated is not None
    assert updated.purpose == "filter"
    assert updated.expression["value"] == 200
    assert store.delete_signal_expression("strategy-a", created.id) is True
    assert store.get_signal_expression("strategy-a", created.id) is None


def test_strategy_store_delete_cascades_signal_expressions(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(id="strategy-a", name="Strategy A")
    created = store.create_signal_expression(
        strategy_id="strategy-a",
        expression={"type": "price_above", "symbol": "self", "value": 100},
    )
    assert created is not None

    assert store.delete("strategy-a") is True
    assert store.get("strategy-a") is None
    assert store.get_signal_expression("strategy-a", created.id) is None


def test_strategy_store_persists_delay_and_pending_execution(tmp_path):
    db_path = str(tmp_path / "strategies.db")
    store = StrategyStore(db_path)
    created = store.create(
        id="strategy-a",
        name="Strategy A",
        execution_delay_seconds=30,
    )
    pending = PendingStrategyExecution(
        correlation_id="delay-a",
        strategy_id=created.id,
        inst_id="ETH-USDT-SWAP",
        triggered_at="2026-07-01T00:00:00+00:00",
        due_at="2026-07-01T00:00:30+00:00",
        evidence_json='{"matched":true}',
    )

    assert created.execution_delay_seconds == 30
    assert store.schedule_pending_execution(pending) is True
    assert store.schedule_pending_execution(pending) is False
    reopened = StrategyStore(db_path)
    assert reopened.get(created.id).execution_delay_seconds == 30
    assert reopened.list_pending_executions()[0].evidence == {"matched": True}
    assert reopened.list_pending_executions(due_at="2026-07-01T00:00:29+00:00") == []
    assert len(reopened.list_pending_executions(due_at="2026-07-01T00:00:30+00:00")) == 1
    assert reopened.delete_pending_execution("delay-a") is True
