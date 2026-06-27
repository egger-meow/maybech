import json

import pytest

from src.trading.audit_event_store import AuditEventStore
from src.trading.execution_allocation import (
    ConfirmedExecutionFill,
    ExecutionAllocationService,
)
from src.trading.logical_position_store import LogicalPositionRecord, LogicalPositionStore
from src.trading.trade_store import TradeRecord, TradeStore


def _stores(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    audit_store = AuditEventStore(db_path)
    return trade_store, position_store, audit_store


def test_execution_allocator_matches_order_and_handles_multiple_partial_fills(tmp_path):
    trade_store, position_store, audit_store = _stores(tmp_path)
    trade = TradeRecord(
        id="trade-a",
        strategy_id="strategy-a",
        status="pending_open",
        metadata_json=json.dumps(
            {
                "correlation_id": "decision-a",
                "exchange_order_id": "order-a",
                "expected_quantity": 0.1,
                "order_action": "open",
            }
        ),
    )
    trade_store.save_trade(trade)
    position_store.save(LogicalPositionRecord.from_trade(trade))
    service = ExecutionAllocationService(trade_store, position_store, audit_store)

    first = service.ingest(
        ConfirmedExecutionFill(
            fill_id="fill-a",
            exchange_order_id="order-a",
            quantity=0.04,
            price=2000,
            confirmation_source="okx_fill",
        )
    )
    second = service.ingest(
        ConfirmedExecutionFill(
            fill_id="fill-b",
            exchange_order_id="order-a",
            quantity=0.06,
            price=2100,
            confirmation_source="okx_fill",
        )
    )
    duplicate = service.ingest(
        ConfirmedExecutionFill(
            fill_id="fill-b",
            exchange_order_id="order-a",
            quantity=0.06,
            price=2100,
            confirmation_source="okx_fill",
        )
    )

    assert first.execution_status == "partially_filled"
    assert first.position.exchange_order_id == "order-a"
    assert second.execution_status == "filled"
    assert second.position.opened_quantity == 0.1
    assert second.position.entry_price == 2060.0
    assert second.position.exchange_order_id == ""
    assert duplicate.idempotent is True
    assert len(position_store.list_allocations("trade-a")) == 2
    assert len(audit_store.list(event_type="position.allocation_confirmed")) == 2


def test_execution_allocator_rejects_unmatched_order(tmp_path):
    trade_store, position_store, audit_store = _stores(tmp_path)
    service = ExecutionAllocationService(trade_store, position_store, audit_store)

    with pytest.raises(LookupError, match="No logical position"):
        service.ingest(
            ConfirmedExecutionFill(
                fill_id="fill-a",
                exchange_order_id="unknown",
                quantity=0.1,
                price=2000,
                confirmation_source="okx_fill",
            )
        )
