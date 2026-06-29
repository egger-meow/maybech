from src.daemon.events import RuntimeState
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
        self.store.update_protection(position_id, status="active")
        return self.store.get(position_id)


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
    assert position.status == "closed"
    assert candle_manager.calls == [{"inst_id": "ETH-USDT-SWAP", "bar": "1m", "limit": 30}]
    intent = service.runtime.get_value("position_manager.intents")[0]
    assert intent["condition_id"] == "volume-spike"
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
