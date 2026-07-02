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


def test_duplicate_fill_keeps_original_correlation_after_position_advances(tmp_path):
    trade_store, position_store, audit_store = _stores(tmp_path)
    position_store.save(
        LogicalPositionRecord(
            id="unit-a",
            status="pending_open",
            exchange_order_id="order-a",
            metadata_json='{"correlation_id":"open-decision","order_action":"open"}',
        )
    )
    service = ExecutionAllocationService(trade_store, position_store, audit_store)
    fill = ConfirmedExecutionFill(
        fill_id="fill-a",
        exchange_order_id="order-a",
        quantity=0.1,
        price=2000,
        confirmation_source="okx_fill",
    )

    service.ingest(fill)
    position_store.merge_metadata("unit-a", {"correlation_id": "reduce-decision"})
    duplicate = service.ingest(fill)

    assert duplicate.idempotent is True
    assert len(position_store.list_allocations("unit-a")) == 1


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


def test_execution_allocator_recovers_position_by_client_order_id(tmp_path):
    trade_store, position_store, audit_store = _stores(tmp_path)
    position_store.save(
        LogicalPositionRecord(
            id="unit-a",
            status="pending_open",
            client_order_id="entryclient1",
            metadata_json='{"expected_quantity":0.1,"order_action":"open"}',
        )
    )
    service = ExecutionAllocationService(trade_store, position_store, audit_store)

    result = service.ingest(
        ConfirmedExecutionFill(
            fill_id="fill-a",
            exchange_order_id="order-a",
            client_order_id="entryclient1",
            quantity=0.1,
            price=2000,
            confirmation_source="okx_fill",
        )
    )

    assert result.position.status == "open"
    assert result.position.opened_quantity == 0.1
    assert position_store.list_allocations("unit-a")[0].exchange_order_id == "order-a"


def test_emergency_close_fill_waits_for_late_open_fill(tmp_path):
    trade_store, position_store, audit_store = _stores(tmp_path)
    trade = TradeRecord(
        id="trade-a",
        strategy_id="strategy-a",
        status="pending_open",
        metadata_json=json.dumps(
            {
                "correlation_id": "decision-a",
                "expected_quantity": 1,
                "order_action": "open",
            }
        ),
    )
    trade_store.save_trade(trade)
    position = LogicalPositionRecord.from_trade(trade)
    position.client_order_id = "entry-client"
    position_store.save(position)
    position_store.link_exchange_order(
        position.id,
        client_order_id="entry-client",
        exchange_order_id="entry-order",
    )
    position_store.claim_pending_execution(
        position.id,
        expected_status="pending_open",
        status="closing",
        client_order_id="close-client",
        metadata={
            "correlation_id": "decision-a",
            "order_action": "close",
            "previous_exchange_order_id": "entry-order",
            "emergency_close": True,
        },
    )
    position_store.mark_pending_execution(
        position.id,
        status="closing",
        exchange_order_id="close-order",
        client_order_id="close-client",
        metadata={"execution_status": "emergency_close_submitted"},
    )
    service = ExecutionAllocationService(trade_store, position_store, audit_store)

    close_result = service.ingest(
        ConfirmedExecutionFill(
            fill_id="close-fill",
            exchange_order_id="close-order",
            quantity=1,
            price=1990,
            confirmation_source="okx_fill",
        )
    )
    deferred = position_store.get_allocation("close-fill")
    open_result = service.ingest(
        ConfirmedExecutionFill(
            fill_id="open-fill",
            exchange_order_id="entry-order",
            quantity=1,
            price=2000,
            confirmation_source="okx_fill",
        )
    )

    assert close_result.execution_status == "close_fill_deferred"
    assert deferred.applied is False
    assert open_result.execution_status == "closed"
    assert open_result.position.opened_quantity == 1
    assert open_result.position.remaining_quantity == 0
    assert open_result.position.status == "closed"
    assert json.loads(open_result.position.metadata_json)["execution_status"] == "closed"
    assert all(allocation.applied for allocation in position_store.list_allocations(position.id))
    assert len(audit_store.list(event_type="position.allocation_deferred")) == 1
    assert len(audit_store.list(event_type="position.allocation_confirmed")) == 2
