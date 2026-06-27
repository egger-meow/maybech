from src.trading.strategy_store import StrategyRecord, StrategyStore


def test_strategy_store_records_schema_version(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))

    assert store.applied_schema_versions() == [1]


def test_strategy_store_creates_updates_and_lists_records(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    created = store.create(
        id="momentum_swap",
        name="Momentum Swap",
        kind="momentum",
        enabled=True,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "volume_price_gap", "k_long": 10},
        default_rules={"stop_win_ratio": 1.5},
    )

    updated = store.update(
        created.id,
        enabled=False,
        target_instruments=["ETH-USDT-SWAP", "SOL-USDT-SWAP"],
    )

    assert updated.enabled is False
    assert updated.target_instruments == ["ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    assert updated.entry_signal["k_long"] == 10
    assert store.list()[0].id == "momentum_swap"


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
        kind="momentum",
        enabled=True,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "runtime"},
        default_rules={},
    )

    assert existing.name == "Custom Strategy"
    assert existing.enabled is False
    assert existing.target_instruments == ["BTC-USDT-SWAP"]


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
