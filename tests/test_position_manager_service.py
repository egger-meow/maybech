from src.daemon.events import RuntimeState
from src.daemon.execution_fill_service import ExecutionFillService
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.daemon.position_manager_service import PositionManagerService
from src.trading.audit_event_store import AuditEventStore
from src.trading.execution_allocation import (
    ConfirmedExecutionFill,
    ExecutionAllocationService,
)
from src.trading.logical_position_store import (
    LogicalPositionProtection,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.position_protection import PositionProtectionError
from src.trading.rules import PositionRule, RuleGroup
from src.trading.trade_store import TradeRecord, TradeStore


class FakeCandleManager:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []

    def fetch(self, inst_id: str, bar: str = "1m", limit: int = 100):
        self.calls.append({"inst_id": inst_id, "bar": bar, "limit": limit})
        return self.frames[(inst_id, bar)]


class FakeCloseExecutor:
    def __init__(self, result, sequence=None):
        self.result = result
        self.calls = []
        self.sequence = sequence

    def close_position(self, **kwargs):
        if self.sequence is not None:
            self.sequence.append("close")
        self.calls.append(kwargs)
        return self.result


class FakeProtectionService:
    def __init__(self, store, sequence):
        self.store = store
        self.sequence = sequence
        self.restores = []

    def cancel_for_close(self, position_id, *, reason):
        self.sequence.append("cancel_protection")
        self.store.update_protection(position_id, status="canceled")
        return True

    def protect(self, position_id):
        self.sequence.append("restore_protection")
        self.restores.append(position_id)
        position = self.store.get(position_id)
        remaining = position.remaining_quantity or position.opened_quantity
        self.store.update_protection(
            position_id,
            status="active",
            quantity=remaining,
        )
        return position

    def verify_active(self, position_id):
        position = self.store.get(position_id)
        protection = self.store.get_protection(position_id)
        remaining = position.remaining_quantity or position.opened_quantity
        if (
            protection is None
            or protection.status != "active"
            or protection.quantity != remaining
        ):
            raise PositionProtectionError("protection is not exact")
        return protection


def _service_with_triggered_rule(tmp_path, *, dry_run: bool) -> tuple[PositionManagerService, TradeStore, str]:
    store = TradeStore(str(tmp_path / "trades.db"))
    trade = TradeRecord(
        id="trade-1",
        strategy_id="strategy-a",
        inst_id="ETH-USDT-SWAP",
        side="long",
        entry_price=100.0,
    )
    store.save_trade(trade)
    LogicalPositionStore(store.db_path).save(
        LogicalPositionRecord(
            id=trade.id,
            source="strategy",
            strategy_id=trade.strategy_id,
            trade_id=trade.id,
            inst_id=trade.inst_id,
            side=trade.side,
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=trade.entry_price,
            entry_time=trade.entry_time,
        )
    )
    store.attach_rule_group(
        trade.id,
        RuleGroup(
            id="take-profit",
            name="take profit",
            operator="and",
            rules=[
                PositionRule(
                    target="self",
                    metric="price",
                    operator="greater_than",
                    value=110.0,
                )
            ],
        ),
    )

    service = PositionManagerService(store, dry_run=dry_run)
    service.runtime = RuntimeState()
    service.runtime.set_value("market.btc_regime", {"price": "65000"})
    service.runtime.set_value(
        "account.snapshot",
        {
            "positions": [
                {"inst_id": "ETH-USDT-SWAP", "mark_price": "120"}
            ]
        },
    )
    return service, store, trade.id


def test_position_manager_closes_triggered_trade_in_dry_run(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=True)

    service.tick()

    trade = store.get_trade(trade_id)
    assert trade is not None
    assert trade.status == "closed"
    assert trade.exit_price == 120.0
    position_store = LogicalPositionStore(store.db_path)
    position = position_store.get(trade_id)
    assert position.status == "closed"
    assert position.remaining_quantity == 0.0
    allocations = position_store.list_allocations(trade_id)
    assert allocations[0].action == "close"
    assert allocations[0].quantity == 0.1
    assert service.runtime.get_value("position_manager.intents")[0]["action"] == "closed"


def test_position_manager_does_not_close_live_trade_without_executor(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=False)

    service.tick()

    trade = store.get_trade(trade_id)
    assert trade is not None
    assert trade.status == "open"
    assert LogicalPositionStore(store.db_path).list_allocations(trade_id) == []

    intents = service.runtime.get_value("position_manager.intents")
    assert intents[0]["action"] == "manual_close_required"

    events = service.runtime.events.recent(event_type="position.close_blocked")
    assert len(events) == 1
    assert events[0].payload["trade_id"] == trade_id


def test_live_close_submission_waits_for_confirmed_partial_fills(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=False)
    close_executor = FakeCloseExecutor({"ordId": "close-order-a"})
    service.close_executor = close_executor

    service.tick()

    position_store = LogicalPositionStore(store.db_path)
    submitted = position_store.get(trade_id)
    intent = service.runtime.get_value("position_manager.intents")[0]
    assert intent["action"] == "close_submitted"
    assert submitted.status == "closing"
    assert submitted.remaining_quantity == 0.1
    assert submitted.exchange_order_id == "close-order-a"
    assert submitted.client_order_id
    assert store.get_trade(trade_id).status == "open"
    assert position_store.list_allocations(trade_id) == []
    assert close_executor.calls == [
        {
            "inst_id": "ETH-USDT-SWAP",
            "position_side": "long",
            "quantity": 0.1,
            "client_order_id": submitted.client_order_id,
            "pos_side": "",
        }
    ]

    allocator = ExecutionAllocationService(trade_store=store, position_store=position_store)
    partial = allocator.ingest(
        ConfirmedExecutionFill(
            fill_id="close-fill-1",
            exchange_order_id="close-order-a",
            quantity=0.04,
            price=120,
            confirmation_source="okx_fill",
        )
    )
    completed = allocator.ingest(
        ConfirmedExecutionFill(
            fill_id="close-fill-2",
            exchange_order_id="close-order-a",
            quantity=0.06,
            price=119,
            confirmation_source="okx_fill",
        )
    )

    assert partial.execution_status == "reduced"
    assert partial.position.status == "closing"
    assert partial.position.remaining_quantity == 0.06
    assert completed.execution_status == "closed"
    assert completed.position.remaining_quantity == 0.0
    assert store.get_trade(trade_id).status == "closed"
    audit_types = {
        event.type
        for event in AuditEventStore(store.db_path).list(position_id=trade_id)
    }
    assert "position.close_requested" in audit_types
    assert "position.close_submitted" in audit_types
    assert "position.allocation_confirmed" in audit_types


def test_dry_run_reduce_changes_only_requested_logical_quantity(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=True)

    result = service.request_reduce(
        trade_id,
        quantity=0.04,
        reason="manual risk reduction",
    )

    position_store = LogicalPositionStore(store.db_path)
    position = position_store.get(trade_id)
    assert result["action"] == "reduced"
    assert position.status == "open"
    assert position.remaining_quantity == 0.06
    assert store.get_trade(trade_id).status == "open"
    allocation = position_store.list_allocations(trade_id)[0]
    assert allocation.action == "reduce"
    assert allocation.quantity == 0.04
    assert len(AuditEventStore(store.db_path).list(event_type="position.reduced")) == 1


def test_live_reduce_waits_for_target_fills_then_restores_remaining_protection(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=False)
    position_store = LogicalPositionStore(store.db_path)
    position_store.save_protection(
        LogicalPositionProtection(
            position_id=trade_id,
            kind="attached_stop",
            algo_id="algo-a",
            algo_client_order_id="algo-client-a",
            quantity=0.1,
            stop_loss=90,
        )
    )
    sequence = []
    service.close_executor = FakeCloseExecutor(
        {"ordId": "reduce-order-a"},
        sequence=sequence,
    )
    service.protection_service = FakeProtectionService(position_store, sequence)

    submitted = service.request_reduce(
        trade_id,
        quantity=0.04,
        reason="manual risk reduction",
    )

    pending = position_store.get(trade_id)
    assert submitted["action"] == "reduce_submitted"
    assert pending.status == "reducing"
    assert pending.remaining_quantity == 0.1
    assert position_store.get_execution_order("reduce-order-a")["action"] == "reduce"
    assert sequence == ["cancel_protection", "close"]
    assert service.close_executor.calls[0]["quantity"] == 0.04

    allocator = ExecutionAllocationService(
        trade_store=store,
        position_store=position_store,
    )
    partial = allocator.ingest(
        ConfirmedExecutionFill(
            fill_id="reduce-fill-1",
            exchange_order_id="reduce-order-a",
            quantity=0.02,
            price=120,
            confirmation_source="okx_fill",
        )
    )
    completed = allocator.ingest(
        ConfirmedExecutionFill(
            fill_id="reduce-fill-2",
            exchange_order_id="reduce-order-a",
            quantity=0.02,
            price=119,
            confirmation_source="okx_fill",
        )
    )

    assert partial.execution_status == "partially_reduced"
    assert partial.position.status == "reducing"
    assert partial.position.remaining_quantity == 0.08
    assert partial.position.exchange_order_id == "reduce-order-a"
    assert completed.execution_status == "reduced"
    assert completed.position.status == "open"
    assert completed.position.remaining_quantity == 0.06
    assert completed.position.exchange_order_id == ""
    assert position_store.get_protection(trade_id).status == "canceled"

    fill_service = ExecutionFillService(
        client=object(),
        allocator=allocator,
        protection_service=service.protection_service,
    )
    fill_status = fill_service._empty_status()
    fill_service._reconcile_owned_protections(fill_status)
    protection = position_store.get_protection(trade_id)
    assert protection.status == "active"
    assert protection.quantity == 0.06
    assert fill_status["protection_rearmed"] == 1
    audit_types = {
        event.type
        for event in AuditEventStore(store.db_path).list(position_id=trade_id)
    }
    assert "position.reduce_requested" in audit_types
    assert "position.reduce_submitted" in audit_types


def test_unknown_reduce_submission_reuses_intent_and_restores_protection(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=False)
    position_store = LogicalPositionStore(store.db_path)
    position_store.save_protection(
        LogicalPositionProtection(
            position_id=trade_id,
            kind="attached_stop",
            algo_id="algo-a",
            algo_client_order_id="algo-client-a",
            quantity=0.1,
            stop_loss=90,
        )
    )
    sequence = []
    service.close_executor = FakeCloseExecutor({}, sequence=sequence)
    service.protection_service = FakeProtectionService(position_store, sequence)

    submitted = service.request_reduce(
        trade_id,
        quantity=0.04,
        reason="manual risk reduction",
    )
    assert submitted["action"] == "reduce_submission_pending"
    client_order_id = position_store.get(trade_id).client_order_id

    service.tick()
    service.tick()
    service.tick()

    recovered = position_store.get(trade_id)
    assert recovered.status == "open"
    assert recovered.remaining_quantity == 0.1
    assert recovered.client_order_id == ""
    assert position_store.get_protection(trade_id).status == "active"
    assert all(
        call["client_order_id"] == client_order_id
        for call in service.close_executor.calls
    )
    assert len({call["client_order_id"] for call in service.close_executor.calls}) == 1
    audit_types = {
        event.type
        for event in AuditEventStore(store.db_path).list(position_id=trade_id)
    }
    assert "position.reduce_submission_unknown" in audit_types
    assert "position.reduce_submission_failed" in audit_types


def test_failed_live_close_submission_releases_position_claim(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=False)
    service.close_executor = FakeCloseExecutor({})

    service.tick()

    position = LogicalPositionStore(store.db_path).get(trade_id)
    intent = service.runtime.get_value("position_manager.intents")[0]
    assert intent["action"] == "close_submission_failed"
    assert position.status == "open"
    assert position.exchange_order_id == ""
    assert position.client_order_id == ""
    assert store.get_trade(trade_id).status == "open"


def test_live_close_cancels_owned_protection_before_submission(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=False)
    position_store = LogicalPositionStore(store.db_path)
    position_store.save_protection(
        LogicalPositionProtection(
            position_id=trade_id,
            kind="attached_stop",
            algo_id="algo-a",
            algo_client_order_id="algo-client-a",
            quantity=0.1,
            stop_loss=90,
        )
    )
    sequence = []
    service.close_executor = FakeCloseExecutor(
        {"ordId": "close-order-a"},
        sequence=sequence,
    )
    service.protection_service = FakeProtectionService(position_store, sequence)

    service.tick()

    assert sequence == ["cancel_protection", "close"]
    assert position_store.get_protection(trade_id).status == "canceled"
    assert position_store.get(trade_id).status == "closing"


def test_failed_close_rearms_owned_protection(tmp_path):
    service, store, trade_id = _service_with_triggered_rule(tmp_path, dry_run=False)
    position_store = LogicalPositionStore(store.db_path)
    position_store.save_protection(
        LogicalPositionProtection(
            position_id=trade_id,
            kind="attached_stop",
            algo_id="algo-a",
            algo_client_order_id="algo-client-a",
            quantity=0.1,
            stop_loss=90,
        )
    )
    sequence = []
    service.close_executor = FakeCloseExecutor({}, sequence=sequence)
    service.protection_service = FakeProtectionService(position_store, sequence)

    service.tick()

    assert sequence == ["cancel_protection", "close"]
    assert position_store.get_protection(trade_id).status == "canceled"
    assert position_store.get(trade_id).status == "closing"

    service.tick()
    service.tick()
    service.tick()

    assert sequence == [
        "cancel_protection",
        "close",
        "close",
        "close",
        "restore_protection",
    ]
    assert position_store.get_protection(trade_id).status == "active"
    assert position_store.get(trade_id).status == "open"


def test_position_manager_closes_trade_from_logical_close_condition(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    trade = TradeRecord(
        id="trade-signal",
        strategy_id="strategy-a",
        inst_id="ETH-USDT-SWAP",
        side="long",
        entry_price=100.0,
    )
    store.save_trade(trade)
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(
        LogicalPositionRecord(
            id=trade.id,
            source="strategy",
            strategy_id=trade.strategy_id,
            trade_id=trade.id,
            inst_id=trade.inst_id,
            side=trade.side,
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=trade.entry_price,
            entry_time=trade.entry_time,
        )
    )
    position_store.create_close_condition(
        id="take-profit-signal",
        position_id=trade.id,
        purpose="take_profit",
        expression={"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 110},
    )
    service = PositionManagerService(store, dry_run=True)
    service.runtime = RuntimeState()
    service.runtime.set_value("market.btc_regime", {"price": "65000"})
    service.runtime.set_value(
        "account.snapshot",
        {"positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "120"}]},
    )

    service.tick()

    closed = store.get_trade(trade.id)
    assert closed.status == "closed"
    assert closed.exit_reason == "close_condition_fired:take_profit:take-profit-signal"
    position = position_store.get(trade.id)
    assert position.status == "closed"
    assert position.remaining_quantity == 0.0
    allocation = position_store.list_allocations(trade.id)[0]
    assert allocation.action == "close"
    assert allocation.quantity == 0.1
    metadata = allocation.metadata_json
    assert "take-profit-signal" in metadata
    intent = service.runtime.get_value("position_manager.intents")[0]
    assert intent["action"] == "closed"
    assert intent["trigger_type"] == "close_condition"
    assert intent["condition_id"] == "take-profit-signal"
    audit_events = AuditEventStore(store.db_path).list(position_id=trade.id)
    audit_types = {event.type for event in audit_events}
    assert "position.close_condition_evaluated" in audit_types
    assert "position.closed" in audit_types


def test_position_manager_executes_staged_take_profit_once_and_leaves_remainder(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(LogicalPositionRecord(
        id="staged", source="manual", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=4, remaining_quantity=4, entry_price=100, status="open",
    ))
    condition = position_store.create_close_condition(
        id="stage-1", position_id="staged", purpose="take_profit",
        expression={"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 110},
        metadata={"quantity_fraction": 0.25},
    )
    service = PositionManagerService(store, dry_run=True)
    service.runtime = RuntimeState()
    service.runtime.set_value(
        "account.snapshot",
        {"positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "120"}]},
    )

    service.tick()
    service.tick()

    updated = position_store.get("staged")
    completed = position_store.get_close_condition("staged", condition.id)
    assert updated.status == "open"
    assert updated.remaining_quantity == 3
    assert completed.enabled is False
    assert completed.metadata["execution_state"]["status"] == "completed"
    assert len(position_store.list_allocations("staged")) == 1


def test_trailing_stop_is_monotonic_restart_safe_and_stale_fail_closed(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(LogicalPositionRecord(
        id="trail", source="manual", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=1, remaining_quantity=1, entry_price=100, status="open",
    ))
    stop = position_store.create_close_condition(
        id="stop", position_id="trail", purpose="stop_loss",
        expression={"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 90},
    )
    trailing = position_store.create_close_condition(
        id="trailing", position_id="trail", purpose="trailing",
        expression={"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 105},
        metadata={"rule_definition": {
            "style": "trailing_threshold", "action": {"type": "amend_stop"},
            "parameters": {"trailing_kind": "stop", "activation_profit_pct": 0.05, "distance_pct": 0.05, "timeframe": "1m", "stale_after_seconds": 90},
            "evidence": {},
        }},
    )
    observed = datetime.now(timezone.utc).isoformat()
    service = PositionManagerService(store, dry_run=True)
    service.runtime = RuntimeState()
    service.runtime.set_value("account.snapshot", {"observed_at": observed, "positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "106"}]})
    service.tick()
    first_stop = position_store.get_close_condition("trail", stop.id).expression["value"]
    assert first_stop == 100.7

    service.runtime.set_value("account.snapshot", {"observed_at": datetime.now(timezone.utc).isoformat(), "positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "110"}]})
    service.tick()
    tightened = position_store.get_close_condition("trail", stop.id).expression["value"]
    assert tightened == 104.5

    restarted = PositionManagerService(store, dry_run=True)
    restarted.runtime = RuntimeState()
    restarted.runtime.set_value("account.snapshot", {"observed_at": datetime.now(timezone.utc).isoformat(), "positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "108"}]})
    restarted.tick()
    assert position_store.get_close_condition("trail", stop.id).expression["value"] == tightened
    assert position_store.get_close_condition("trail", trailing.id).metadata["trailing_state"]["water_price"] == "110.0"

    restarted.runtime.set_value("account.snapshot", {"observed_at": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(), "positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "120"}]})
    restarted.tick()
    assert position_store.get_close_condition("trail", stop.id).expression["value"] == tightened
    assert position_store.get_close_condition("trail", trailing.id).metadata["trailing_state"]["status"] == "stale"


def test_trailing_take_profit_reduces_only_after_retracement(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(LogicalPositionRecord(
        id="trail-tp", source="manual", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=2, remaining_quantity=2, entry_price=100, status="open",
    ))
    rule = position_store.create_close_condition(
        id="trailing-tp", position_id="trail-tp", purpose="trailing",
        expression={"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 105},
        metadata={"rule_definition": {
            "style": "trailing_threshold",
            "action": {"type": "reduce_position", "quantity_fraction": 0.5, "quantity_basis": "initial"},
            "parameters": {"trailing_kind": "take_profit", "activation_profit_pct": 0.05, "distance_pct": 0.05, "timeframe": "1m", "stale_after_seconds": 90},
            "evidence": {},
        }},
    )
    service = PositionManagerService(store, dry_run=True)
    service.runtime = RuntimeState()
    service.runtime.set_value("account.snapshot", {"observed_at": datetime.now(timezone.utc).isoformat(), "positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "110"}]})
    service.tick()
    assert position_store.get("trail-tp").remaining_quantity == 2
    assert position_store.get_close_condition("trail-tp", rule.id).expression["value"] == 104.5

    service.runtime.set_value("account.snapshot", {"observed_at": datetime.now(timezone.utc).isoformat(), "positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "104"}]})
    service.tick()
    assert position_store.get("trail-tp").remaining_quantity == 1
    assert position_store.get_close_condition("trail-tp", rule.id).enabled is False
    assert position_store.list_allocations("trail-tp")[0].action == "reduce"


def test_live_trailing_stop_uses_confirmed_protection_amend_lifecycle(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(LogicalPositionRecord(
        id="live-trail", source="manual", inst_id="ETH-USDT-SWAP", side="short",
        opened_quantity=1, remaining_quantity=1, entry_price=100, status="open",
    ))
    stop = position_store.create_close_condition(
        id="stop", position_id="live-trail", purpose="stop_loss",
        expression={"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 110},
    )
    trailing = position_store.create_close_condition(
        id="trailing", position_id="live-trail", purpose="trailing",
        expression={"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 95},
        metadata={"rule_definition": {
            "style": "trailing_threshold", "action": {"type": "amend_stop"},
            "parameters": {"trailing_kind": "stop", "activation_profit_pct": 0.05, "distance_pct": 0.03, "timeframe": "1m", "stale_after_seconds": 90},
            "evidence": {},
        }},
    )

    class FakeProtection:
        def __init__(self):
            self.calls = []

        def amend_stop_condition(self, position_id, condition_id, **kwargs):
            self.calls.append((position_id, condition_id, kwargs))
            return position_store.get(position_id)

    protection = FakeProtection()
    service = PositionManagerService(store, dry_run=False, protection_service=protection)
    service.runtime = RuntimeState()
    service.runtime.set_value("account.snapshot", {"observed_at": datetime.now(timezone.utc).isoformat(), "positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "90"}]})
    service.tick()

    assert protection.calls[0][0:2] == ("live-trail", stop.id)
    assert protection.calls[0][2]["expression"]["value"] == 92.7
    assert protection.calls[0][2]["intent_metadata"]["operation"] == "trailing_stop"
    state = position_store.get_close_condition("live-trail", trailing.id).metadata["trailing_state"]
    assert state["status"] == "active"
    assert Decimal(state["last_applied_stop"]) == Decimal("92.7")


def test_position_manager_applies_cost_adjusted_break_even_once_in_dry_run(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(LogicalPositionRecord(
        id="break-even", source="manual", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=1, remaining_quantity=1, entry_price=100, status="open",
    ))
    stop = position_store.create_close_condition(
        id="stop", position_id="break-even", purpose="stop_loss",
        expression={"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 90},
    )
    rule = position_store.create_close_condition(
        id="break-even-rule", position_id="break-even", purpose="break_even",
        expression={"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 102},
        metadata={"rule_definition": {
            "style": "break_even_threshold", "action": {"type": "amend_stop"},
            "parameters": {
                "entry_fee_rate": 0.001, "exit_fee_rate": 0.001,
                "slippage_rate": 0.001, "lock_in_pct": 0,
            }, "evidence": {},
        }},
    )
    service = PositionManagerService(store, dry_run=True)
    service.runtime = RuntimeState()
    service.runtime.set_value(
        "account.snapshot",
        {"positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "105"}]},
    )

    service.tick()
    service.tick()

    applied_stop = position_store.get_close_condition("break-even", stop.id)
    applied_rule = position_store.get_close_condition("break-even", rule.id)
    assert applied_stop.expression["value"] > 100
    assert applied_rule.enabled is False
    assert applied_rule.metadata["break_even_state"]["status"] == "applied"
    assert applied_stop.metadata["break_even"]["costs"]["slippage_rate"] == "0.001"
    assert service.runtime.get_value("position_manager.intents")[0]["action"] == "hold"
    assert len(AuditEventStore(store.db_path).list(event_type="position.break_even_applied")) == 1


def test_break_even_armed_state_survives_service_restart_until_cost_target_is_met(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(LogicalPositionRecord(
        id="restart-be", source="manual", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=1, remaining_quantity=1, entry_price=100, status="open",
    ))
    position_store.create_close_condition(
        id="stop", position_id="restart-be", purpose="stop_loss",
        expression={"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 90},
    )
    rule = position_store.create_close_condition(
        id="be", position_id="restart-be", purpose="break_even",
        expression={"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 100.1},
    )
    first = PositionManagerService(store, dry_run=True)
    first.runtime = RuntimeState()
    first.runtime.set_value(
        "account.snapshot",
        {"positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "100.15"}]},
    )

    first.tick()

    armed = position_store.get_close_condition("restart-be", rule.id)
    assert armed.enabled is True
    assert armed.metadata["break_even_state"]["status"] == "armed"

    restarted = PositionManagerService(store, dry_run=True)
    restarted.runtime = RuntimeState()
    restarted.runtime.set_value(
        "account.snapshot",
        {"positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "101"}]},
    )
    restarted.tick()

    applied = position_store.get_close_condition("restart-be", rule.id)
    assert applied.enabled is False
    assert applied.metadata["break_even_state"]["status"] == "applied"


def test_live_break_even_rule_uses_protection_amend_lifecycle_not_close_executor(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(LogicalPositionRecord(
        id="live-be", source="manual", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=1, remaining_quantity=1, entry_price=100, status="open",
    ))
    stop = position_store.create_close_condition(
        id="stop", position_id="live-be", purpose="stop_loss",
        expression={"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 90},
    )
    rule = position_store.create_close_condition(
        id="be", position_id="live-be", purpose="break_even",
        expression={"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 102},
        metadata={"rule_definition": {
            "style": "break_even_threshold", "action": {"type": "amend_stop"},
            "parameters": {"lock_in_pct": 0.01, "slippage_rate": 0.001},
            "evidence": {},
        }},
    )

    class FakeProtection:
        def __init__(self):
            self.calls = []

        def move_to_break_even(self, position_id, condition_id, **kwargs):
            self.calls.append((position_id, condition_id, kwargs))
            return position_store.get(position_id)

    protection = FakeProtection()
    service = PositionManagerService(
        store, dry_run=False, close_executor=None, protection_service=protection
    )
    service.runtime = RuntimeState()
    service.runtime.set_value(
        "account.snapshot",
        {"positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "105"}]},
    )

    service.tick()

    assert protection.calls[0][0:2] == ("live-be", stop.id)
    assert protection.calls[0][2]["lock_in_pct"] == Decimal("0.01")
    assert protection.calls[0][2]["slippage_rate"] == Decimal("0.001")
    assert position_store.get_close_condition("live-be", rule.id).enabled is False
    assert not position_store.list_allocations("live-be")


def test_position_manager_closes_manual_logical_position_without_trade_in_dry_run(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(
        LogicalPositionRecord(
            id="manual-unit",
            source="manual",
            inst_id="BTC-USDT-SWAP",
            side="short",
            opened_quantity=0.02,
            remaining_quantity=0.02,
            entry_price=65000,
        )
    )
    position_store.create_close_condition(
        id="manual-stop",
        position_id="manual-unit",
        purpose="stop_loss",
        expression={"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65100},
    )
    service = PositionManagerService(store, dry_run=True)
    service.runtime = RuntimeState()
    service.runtime.set_value("market.btc_regime", {"price": "65200"})

    service.tick()

    position = position_store.get("manual-unit")
    assert position.status == "closed"
    assert position.remaining_quantity == 0.0
    allocations = position_store.list_allocations("manual-unit")
    assert allocations[0].quantity == 0.02
    assert store.get_open_trades() == []
    assert service.runtime.get_value("position_manager.intents")[0]["trade_id"] is None


def test_position_manager_blocks_live_close_condition_without_executor(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(
        LogicalPositionRecord(
            id="manual-unit",
            source="manual",
            inst_id="BTC-USDT-SWAP",
            side="short",
            opened_quantity=0.02,
            remaining_quantity=0.02,
            entry_price=65000,
        )
    )
    position_store.create_close_condition(
        id="manual-stop",
        position_id="manual-unit",
        purpose="stop_loss",
        expression={"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65100},
    )
    service = PositionManagerService(store, dry_run=False)
    service.runtime = RuntimeState()
    service.runtime.set_value("market.btc_regime", {"price": "65200"})

    service.tick()

    position = position_store.get("manual-unit")
    assert position.status == "open"
    assert position_store.list_allocations("manual-unit") == []
    intent = service.runtime.get_value("position_manager.intents")[0]
    assert intent["action"] == "manual_close_required"
    assert intent["condition_id"] == "manual-stop"
    events = service.runtime.events.recent(event_type="position.close_blocked")
    assert events[0].payload["condition_id"] == "manual-stop"
    audit_events = AuditEventStore(store.db_path).list(position_id="manual-unit")
    audit_types = {event.type for event in audit_events}
    assert "position.close_condition_evaluated" in audit_types
    assert "position.close_blocked" in audit_types


def test_position_manager_uses_candles_for_rapid_drop_close_condition(tmp_path):
    import pandas as pd

    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(
        LogicalPositionRecord(
            id="rapid-unit",
            source="manual",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=100,
        )
    )
    position_store.create_close_condition(
        id="rapid-drop",
        position_id="rapid-unit",
        purpose="stop_loss",
        expression={
            "type": "rapid_drop",
            "symbol": "ETH-USDT-SWAP",
            "window_seconds": 300,
            "change_pct": 5,
        },
    )
    candle_manager = FakeCandleManager(
        {
            ("ETH-USDT-SWAP", "1m"): pd.DataFrame(
                [
                    {"timestamp": "2026-01-01T00:00:00Z", "close": 100, "volume": 10},
                    {"timestamp": "2026-01-01T00:01:00Z", "close": 99, "volume": 10},
                    {"timestamp": "2026-01-01T00:02:00Z", "close": 98, "volume": 10},
                    {"timestamp": "2026-01-01T00:03:00Z", "close": 97, "volume": 10},
                    {"timestamp": "2026-01-01T00:04:00Z", "close": 96, "volume": 10},
                    {"timestamp": "2026-01-01T00:05:00Z", "close": 94, "volume": 20},
                ]
            )
        }
    )
    service = PositionManagerService(
        store,
        dry_run=True,
        candle_manager=candle_manager,
        candle_bar="1m",
        candle_limit=20,
    )
    service.runtime = RuntimeState()
    service.runtime.set_value(
        "account.snapshot",
        {"positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "94"}]},
    )

    service.tick()

    position = position_store.get("rapid-unit")
    assert position.status == "closed"
    assert candle_manager.calls == [{"inst_id": "ETH-USDT-SWAP", "bar": "1m", "limit": 20}]
    intent = service.runtime.get_value("position_manager.intents")[0]
    assert intent["condition_id"] == "rapid-drop"
    assert intent["condition_evidence"]["change_pct"] == -6.0


def test_position_manager_uses_candles_for_volume_multiple_close_condition(tmp_path):
    import pandas as pd

    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(
        LogicalPositionRecord(
            id="volume-unit",
            source="manual",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=100,
        )
    )
    position_store.create_close_condition(
        id="volume-spike",
        position_id="volume-unit",
        purpose="manual_review",
        expression={
            "type": "volume_multiple",
            "symbol": "ETH-USDT-SWAP",
            "timeframe": "1m",
            "multiplier": 2,
        },
    )
    candle_manager = FakeCandleManager(
        {
            ("ETH-USDT-SWAP", "1m"): pd.DataFrame(
                [
                    {"timestamp": "2026-01-01T00:00:00Z", "close": 100, "volume": 10},
                    {"timestamp": "2026-01-01T00:01:00Z", "close": 101, "volume": 10},
                    {"timestamp": "2026-01-01T00:02:00Z", "close": 102, "volume": 30},
                ]
            )
        }
    )
    service = PositionManagerService(
        store,
        dry_run=True,
        candle_manager=candle_manager,
        candle_bar="5m",
        candle_limit=30,
    )
    service.runtime = RuntimeState()
    service.runtime.set_value(
        "account.snapshot",
        {"positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "102"}]},
    )

    service.tick()

    position = position_store.get("volume-unit")
    assert position.status == "open"
    assert json.loads(position.metadata_json)["requires_manual_review"] is True
    assert candle_manager.calls == [{"inst_id": "ETH-USDT-SWAP", "bar": "1m", "limit": 30}]
    intent = service.runtime.get_value("position_manager.intents")[0]
    assert intent["condition_id"] == "volume-spike"
    assert intent["action"] == "manual_review"
    assert intent["condition_evidence"]["volume_ratio"] == 3.0


def test_position_manager_keeps_running_when_candle_context_fetch_fails(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(
        LogicalPositionRecord(
            id="rapid-unit",
            source="manual",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=100,
        )
    )
    position_store.create_close_condition(
        id="rapid-drop",
        position_id="rapid-unit",
        purpose="stop_loss",
        expression={
            "type": "rapid_drop",
            "symbol": "ETH-USDT-SWAP",
            "window_seconds": 300,
            "change_pct": 5,
        },
    )

    class BrokenCandleManager:
        def fetch(self, inst_id: str, bar: str = "1m", limit: int = 100):
            raise RuntimeError("boom")

    service = PositionManagerService(
        store,
        dry_run=True,
        candle_manager=BrokenCandleManager(),
        candle_bar="1m",
    )
    service.runtime = RuntimeState()
    service.runtime.set_value(
        "account.snapshot",
        {"positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "94"}]},
    )

    service.tick()

    assert position_store.get("rapid-unit").status == "open"
    assert service.runtime.get_value("position_manager.intents")[0]["action"] == "hold"
    events = service.runtime.events.recent(event_type="position.candle_context_error")
    assert events[0].payload["error"] == "boom"
    audit_events = AuditEventStore(store.db_path).list(event_type="position.candle_context_error")
    assert audit_events[0].payload["error"] == "boom"
