import json

import pytest

from src.trading.audit_event_store import AuditEventStore
from src.trading.execution_allocation import (
    ConfirmedExecutionFill,
    ExecutionAllocationService,
)
from src.trading.logical_position_store import LogicalPositionProtection, LogicalPositionRecord, LogicalPositionStore
from src.trading.trade_store import TradeRecord, TradeStore
from src.trading.position_rule_model import materialize_position_rule, normalize_default_rules


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


def test_confirmed_staged_reduce_disables_only_completed_target(tmp_path):
    trade_store, position_store, audit_store = _stores(tmp_path)
    position_store.save(LogicalPositionRecord(
        id="staged", source="strategy", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=4, remaining_quantity=4, entry_price=100, status="reducing",
        metadata_json=json.dumps({"rule_condition_id": "stage-1"}),
    ))
    first = position_store.create_close_condition(
        id="stage-1", position_id="staged", purpose="take_profit",
        expression={"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 110},
        metadata={"quantity_fraction": 0.25},
    )
    second = position_store.create_close_condition(
        id="stage-2", position_id="staged", purpose="take_profit",
        expression={"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 120},
        metadata={"quantity_fraction": 0.25},
    )
    result = ExecutionAllocationService(
        trade_store, position_store, audit_store
    ).ingest(ConfirmedExecutionFill(
        fill_id="reduce-fill", position_id="staged", action="reduce",
        quantity=1, price=111, confirmation_source="okx_fill",
    ))

    assert result.execution_status == "reduced"
    assert position_store.get_close_condition("staged", first.id).enabled is False
    assert position_store.get_close_condition("staged", second.id).enabled is True
    assert position_store.get_close_condition("staged", first.id).metadata["execution_state"]["fill_id"] == "reduce-fill"


def test_open_fill_rematerializes_entry_relative_rules_from_confirmed_average(tmp_path):
    trade_store, position_store, audit_store = _stores(tmp_path)
    position_store.save(LogicalPositionRecord(
        id="relative", source="strategy", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=0, remaining_quantity=0, entry_price=100,
        status="pending_open",
    ))
    template = normalize_default_rules({"close_conditions": [{
        "purpose": "take_profit", "enabled": True,
        "expression": {"type": "entry_relative", "symbol": "self"},
        "metadata": {"rule_definition": {
            "style": "fixed_percent", "action": {"type": "close_position"},
            "parameters": {"offset_pct": "0.10"}, "evidence": {},
        }},
    }]})["close_conditions"][0]
    provisional = materialize_position_rule(
        template, entry_price=100, inst_id="ETH-USDT-SWAP", side="long",
        basis="provisional_order_price",
    )
    condition = position_store.create_close_condition(
        id="relative-target", position_id="relative", purpose="take_profit",
        expression=provisional["expression"], metadata=provisional["metadata"],
    )

    result = ExecutionAllocationService(
        trade_store, position_store, audit_store
    ).ingest(ConfirmedExecutionFill(
        fill_id="open-fill", position_id="relative", action="open",
        quantity=1, price=102, confirmation_source="okx_fill",
    ))

    updated = position_store.get_close_condition("relative", condition.id)
    assert result.position.entry_price == 102
    assert updated.expression["value"] == pytest.approx(112.2)
    assert updated.metadata["materialization"]["basis"] == "confirmed_average_entry"


def test_protected_relative_stop_waits_for_exchange_amend_after_fill(tmp_path):
    trade_store, position_store, audit_store = _stores(tmp_path)
    position_store.save(LogicalPositionRecord(
        id="protected-relative", source="strategy", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=0, remaining_quantity=0, entry_price=100, status="pending_open",
    ))
    template = normalize_default_rules({"close_conditions": [{
        "purpose": "stop_loss", "enabled": True,
        "expression": {"type": "entry_relative", "symbol": "self"},
        "metadata": {"rule_definition": {
            "style": "fixed_percent", "action": {"type": "close_position"},
            "parameters": {"offset_pct": "0.05"}, "evidence": {},
        }},
    }]})["close_conditions"][0]
    provisional = materialize_position_rule(
        template, entry_price=100, inst_id="ETH-USDT-SWAP", side="long",
        basis="provisional_order_price",
    )
    condition = position_store.create_close_condition(
        id="relative-stop", position_id="protected-relative", purpose="stop_loss",
        expression=provisional["expression"], metadata=provisional["metadata"],
    )
    position_store.save_protection(LogicalPositionProtection(
        position_id="protected-relative", kind="attached_stop", status="active",
        algo_id="algo", algo_client_order_id="algo-client", quantity=1, stop_loss=95,
    ))

    ExecutionAllocationService(trade_store, position_store, audit_store).ingest(
        ConfirmedExecutionFill(
            fill_id="open-fill", position_id="protected-relative", action="open",
            quantity=1, price=102, confirmation_source="okx_fill",
        )
    )

    updated = position_store.get_close_condition("protected-relative", condition.id)
    pending = updated.metadata["pending_materialization"]
    assert updated.expression["value"] == 95
    assert pending["expression"]["value"] == pytest.approx(96.9)
    assert pending["status"] == "pending_exchange_amend"
    assert json.loads(position_store.get("protected-relative").metadata_json)["protection_materialization_pending"] is True


def test_later_open_fill_cannot_rematerialize_stop_below_confirmed_amendment(tmp_path):
    trade_store, position_store, audit_store = _stores(tmp_path)
    position_store.save(LogicalPositionRecord(
        id="tightened", source="strategy", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=1, remaining_quantity=1, entry_price=100, status="open",
    ))
    template = normalize_default_rules({"close_conditions": [{
        "purpose": "stop_loss", "enabled": True,
        "expression": {"type": "entry_relative", "symbol": "self"},
        "metadata": {"rule_definition": {
            "style": "fixed_percent", "action": {"type": "close_position"},
            "parameters": {"offset_pct": "0.05"}, "evidence": {},
        }},
    }]})["close_conditions"][0]
    provisional = materialize_position_rule(
        template, entry_price=100, inst_id="ETH-USDT-SWAP", side="long",
        basis="provisional_order_price",
    )
    condition = position_store.create_close_condition(
        id="tight-stop", position_id="tightened", purpose="stop_loss",
        expression={**provisional["expression"], "value": 98},
        metadata=provisional["metadata"],
    )
    position_store.save_protection(LogicalPositionProtection(
        position_id="tightened", kind="attached_stop", status="active",
        algo_id="algo", algo_client_order_id="algo-client", quantity=1,
        stop_loss=98,
    ))

    ExecutionAllocationService(trade_store, position_store, audit_store).ingest(
        ConfirmedExecutionFill(
            fill_id="later-open-fill", position_id="tightened", action="open",
            quantity=1, price=102, confirmation_source="okx_fill",
        )
    )

    updated = position_store.get_close_condition("tightened", condition.id)
    assert updated.expression["value"] == 98
    assert "pending_materialization" not in updated.metadata
    events = audit_store.list(
        event_type="position.rule_materialization_skipped",
        position_id="tightened",
    )
    assert len(events) == 1
    assert events[0].payload["candidate_stop"] == pytest.approx(95.95)


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
