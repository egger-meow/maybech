import sqlite3

import pytest

from src.trading.logical_position_store import (
    AllocationConflictError,
    LogicalPositionAllocation,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.trade_store import TradeRecord
from src.trading.sqlite_schema import configure_connection, initialize_schema


def test_logical_position_store_saves_and_lists_independent_units(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    first = LogicalPositionRecord(
        id="unit-a",
        source="strategy",
        strategy_id="momentum_swap",
        inst_id="ETH-USDT-SWAP",
        side="short",
        opened_quantity=0.1,
        remaining_quantity=0.1,
        entry_price=3000,
    )
    second = LogicalPositionRecord(
        id="unit-b",
        source="strategy",
        strategy_id="momentum_swap",
        inst_id="ETH-USDT-SWAP",
        side="short",
        opened_quantity=0.2,
        remaining_quantity=0.2,
        entry_price=3010,
    )

    store.save(first)
    store.save(second)

    positions = store.list(status="open")
    ids = {position.id for position in positions}
    assert ids == {"unit-a", "unit-b"}
    assert store.get("unit-a").entry_price == 3000


def test_logical_position_store_records_schema_version(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))

    assert store.applied_schema_versions() == [2, 3]


def test_logical_position_store_migrates_exchange_order_lookup(tmp_path):
    db_path = str(tmp_path / "positions.db")
    conn = sqlite3.connect(db_path)
    try:
        configure_connection(conn)
        initialize_schema(
            conn,
            schema_sql="""
                CREATE TABLE logical_positions (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    strategy_id TEXT NOT NULL DEFAULT '',
                    trade_id TEXT,
                    inst_id TEXT NOT NULL DEFAULT '',
                    side TEXT NOT NULL DEFAULT '',
                    opened_quantity REAL,
                    remaining_quantity REAL,
                    entry_price REAL NOT NULL DEFAULT 0.0,
                    entry_time TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    exchange_position_key TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """,
            component="logical_positions",
            version=2,
        )
        conn.commit()
    finally:
        conn.close()

    store = LogicalPositionStore(db_path)
    store.save(
        LogicalPositionRecord(
            id="unit-a",
            exchange_order_id="order-a",
            status="pending_open",
        )
    )

    assert store.applied_schema_versions() == [2, 3]
    assert store.get_by_exchange_order_id("order-a").id == "unit-a"


def test_logical_position_store_backfills_from_trade_once(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    trade = TradeRecord(
        id="trade-a",
        strategy_id="momentum_swap",
        inst_id="SOL-USDT-SWAP",
        side="long",
        entry_price=100,
    )

    first = store.ensure_from_trade(trade)
    second = store.ensure_from_trade(trade)

    assert first.id == "trade-a"
    assert second.id == "trade-a"
    assert len(store.list(status="open")) == 1
    assert store.get("trade-a").source == "strategy"
    assert store.get("trade-a").trade_id == "trade-a"


def test_logical_position_store_updates_status_without_merging_units(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    first = LogicalPositionRecord(id="unit-a", inst_id="ETH-USDT-SWAP", side="short", entry_price=3000)
    second = LogicalPositionRecord(id="unit-b", inst_id="ETH-USDT-SWAP", side="short", entry_price=3010)
    store.save(first)
    store.save(second)

    updated = store.update_status("unit-a", status="closing", remaining_quantity=0.05)

    assert updated.status == "closing"
    assert updated.remaining_quantity == 0.05
    assert store.get("unit-b").status == "open"


def test_logical_position_store_records_reduce_allocation(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    store.save(
        LogicalPositionRecord(
            id="unit-a",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.3,
            remaining_quantity=0.3,
            entry_price=3000,
        )
    )

    updated = store.record_allocation(
        LogicalPositionAllocation(
            id="alloc-a",
            position_id="unit-a",
            action="reduce",
            quantity=0.1,
            price=3050,
            exchange_order_id="ord-1",
            reason="partial take profit",
        )
    )

    assert updated.remaining_quantity == 0.2
    allocations = store.list_allocations("unit-a")
    assert len(allocations) == 1
    assert allocations[0].exchange_order_id == "ord-1"


def test_logical_position_store_records_close_allocation(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    store.save(
        LogicalPositionRecord(
            id="unit-a",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=3000,
        )
    )

    updated = store.record_allocation(
        LogicalPositionAllocation(
            position_id="unit-a",
            action="close",
            quantity=0.1,
            price=2990,
            reason="stop loss",
        )
    )

    assert updated.remaining_quantity == 0.0
    assert updated.status == "closed"


def test_logical_position_store_parent_save_preserves_allocations(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    position = LogicalPositionRecord(
        id="unit-a",
        inst_id="ETH-USDT-SWAP",
        side="long",
        opened_quantity=0.2,
        remaining_quantity=0.2,
        entry_price=3000,
    )
    store.save(position)
    store.record_allocation(
        LogicalPositionAllocation(
            id="alloc-a",
            position_id="unit-a",
            action="reduce",
            quantity=0.05,
            price=3050,
        ),
        apply_to_position=False,
    )

    position.entry_price = 3010
    store.save(position)

    allocations = store.list_allocations("unit-a")
    assert len(allocations) == 1
    assert allocations[0].id == "alloc-a"


def test_logical_position_store_manages_close_conditions(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    store.save(LogicalPositionRecord(id="unit-a", inst_id="ETH-USDT-SWAP", side="long", entry_price=3000))

    condition = store.create_close_condition(
        id="stop-loss-a",
        position_id="unit-a",
        purpose="stop_loss",
        expression={"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 2900},
        metadata={"label": "hard stop"},
    )

    assert condition is not None
    listed = store.list_close_conditions("unit-a")
    assert len(listed) == 1
    assert listed[0].id == "stop-loss-a"
    assert listed[0].expression["value"] == 2900
    assert listed[0].metadata["label"] == "hard stop"

    updated = store.update_close_condition(
        "unit-a",
        "stop-loss-a",
        enabled=False,
        expression={"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 2910},
    )

    assert updated.enabled is False
    assert updated.expression["value"] == 2910
    assert store.list_close_conditions("unit-a", enabled=True) == []
    assert store.delete_close_condition("unit-a", "stop-loss-a") is True
    assert store.list_close_conditions("unit-a") == []


def test_logical_position_store_rejects_close_condition_for_missing_position(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))

    condition = store.create_close_condition(
        position_id="missing",
        expression={"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 2900},
    )

    assert condition is None


def test_open_fill_allocations_are_idempotent_and_weight_entry_price(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    store.save(
        LogicalPositionRecord(
            id="unit-a",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.0,
            remaining_quantity=0.0,
            entry_price=0.0,
            status="pending_open",
        )
    )
    first = LogicalPositionAllocation(
        id="fill-a",
        position_id="unit-a",
        action="open",
        quantity=0.04,
        price=2000.0,
        exchange_order_id="order-a",
    )

    store.record_allocation(first)
    duplicate = store.record_allocation(
        LogicalPositionAllocation(
            id="fill-a",
            position_id="unit-a",
            action="open",
            quantity=0.04,
            price=2000.0,
            exchange_order_id="order-a",
        )
    )
    completed = store.record_allocation(
        LogicalPositionAllocation(
            id="fill-b",
            position_id="unit-a",
            action="open",
            quantity=0.06,
            price=2100.0,
            exchange_order_id="order-a",
        )
    )

    assert duplicate.opened_quantity == 0.04
    assert completed.opened_quantity == 0.1
    assert completed.remaining_quantity == 0.1
    assert completed.entry_price == 2060.0
    assert completed.status == "open"
    assert len(store.list_allocations("unit-a")) == 2


def test_conflicting_fill_id_does_not_change_position(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    store.save(
        LogicalPositionRecord(
            id="unit-a",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=2000,
        )
    )
    store.record_allocation(
        LogicalPositionAllocation(
            id="fill-a",
            position_id="unit-a",
            action="reduce",
            quantity=0.02,
            price=2100,
        )
    )

    with pytest.raises(AllocationConflictError):
        store.record_allocation(
            LogicalPositionAllocation(
                id="fill-a",
                position_id="unit-a",
                action="reduce",
                quantity=0.03,
                price=2100,
            )
        )

    assert store.get("unit-a").remaining_quantity == 0.08


def test_allocation_rejects_quantity_above_remaining_position(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    store.save(
        LogicalPositionRecord(
            id="unit-a",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=2000,
        )
    )

    with pytest.raises(ValueError, match="exceeds remaining"):
        store.record_allocation(
            LogicalPositionAllocation(
                id="fill-a",
                position_id="unit-a",
                action="close",
                quantity=0.11,
                price=1900,
            )
        )

    assert store.list_allocations("unit-a") == []
    assert store.get("unit-a").remaining_quantity == 0.1


def test_pending_execution_claim_is_compare_and_set(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "positions.db"))
    store.save(
        LogicalPositionRecord(
            id="unit-a",
            status="open",
            opened_quantity=0.1,
            remaining_quantity=0.1,
        )
    )

    first = store.claim_pending_execution(
        "unit-a",
        expected_status="open",
        status="closing",
        metadata={"correlation_id": "close-a"},
    )
    second = store.claim_pending_execution(
        "unit-a",
        expected_status="open",
        status="closing",
        metadata={"correlation_id": "close-b"},
    )

    assert first.status == "closing"
    assert second is None
