import json
from datetime import datetime, timedelta, timezone

import pytest

from src.daemon.execution_fill_service import ExecutionFillService
from src.daemon.service import DaemonRunner
from src.exchange.fills import normalize_okx_fill
from src.trading.execution_allocation import (
    ConfirmedExecutionFill,
    ExecutionAllocationService,
)
from src.trading.execution_cursor_store import ExecutionCursorStore
from src.trading.logical_position_store import (
    LogicalPositionProtection,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.trade_store import TradeRecord, TradeStore


class FakeFillClient:
    def __init__(self, fills, orders=None):
        self.fills = fills
        self.orders = orders or {}
        self.cancel_calls = []
        self.fill_calls = []

    def get_fills_history(self, inst_type="SWAP", limit="100", after=""):
        assert inst_type == "SWAP"
        assert limit == "100"
        self.fill_calls.append(after)
        if isinstance(self.fills, dict):
            result = self.fills.get(after, [])
            if isinstance(result, Exception):
                raise result
            return result
        return self.fills if not after else []

    def get_order(self, inst_id, order_id="", client_order_id=""):
        order = self.orders.get(order_id or client_order_id)
        return [] if order is None else [order]

    def cancel_order(self, inst_id, order_id):
        self.cancel_calls.append((inst_id, order_id))
        return {"ordId": order_id, "sCode": "0"}


class FakeOrderStream:
    def __init__(self, events=None):
        self.events = list(events or [])
        self.started = False
        self.stopped = False
        self.dropped_events = 0

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def drain(self, *, limit):
        drained = self.events[:limit]
        self.events = self.events[limit:]
        return drained

    def status_dict(self):
        return {
            "enabled": True,
            "connected": self.started and not self.stopped,
            "events_received": 1,
            "reconnects": 0,
            "dropped_events": self.dropped_events,
            "last_message_at": "2026-06-28T00:00:00+00:00",
            "last_error": "",
        }


class FakeRearmProtection:
    def __init__(self, store):
        self.store = store
        self.calls = []

    def protect(self, position_id):
        self.calls.append(position_id)
        position = self.store.get(position_id)
        self.store.update_protection(
            position_id,
            status="active",
            quantity=position.remaining_quantity,
        )
        return self.store.get(position_id)

    def verify_active(self, position_id):
        return self.store.get_protection(position_id)


def _raw_fill(fill_id="fill-a", order_id="order-a", bill_id=None, client_order_id=""):
    return {
        "billId": bill_id or f"bill-{fill_id}",
        "tradeId": fill_id,
        "ordId": order_id,
        "clOrdId": client_order_id,
        "instId": "ETH-USDT-SWAP",
        "side": "buy",
        "posSide": "net",
        "fillSz": "0.1",
        "fillPx": "2000.5",
        "fee": "-0.02",
        "feeCcy": "USDT",
        "ts": "1782518400000",
    }


def test_normalize_okx_fill_maps_confirmed_fields():
    fill = normalize_okx_fill({
        **_raw_fill(), "fillPnl": "-0.00125", "fillPnlCcy": "USDT",
    })

    assert fill.fill_id == "fill-a"
    assert fill.exchange_order_id == "order-a"
    assert fill.quantity == 0.1
    assert fill.price == 2000.5
    assert fill.fee == -0.02
    assert fill.occurred_at.startswith("2026-")
    assert fill.metadata["inst_id"] == "ETH-USDT-SWAP"
    assert fill.metadata["exchange_realized_pnl"] == -0.00125
    assert fill.metadata["exchange_realized_pnl_currency"] == "USDT"


def test_execution_fill_service_applies_private_order_fill_before_rest_history(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="ws-unit",
            inst_id="ETH-USDT-SWAP",
            exchange_order_id="ws-order",
            status="pending_open",
            metadata_json='{"expected_quantity":0.1,"order_action":"open"}',
        )
    )
    event = {
        **_raw_fill("ws-fill", "ws-order"),
        "billId": "",
        "fillFee": "-0.03",
        "fillFeeCcy": "USDT",
        "fillTime": "1782518400000",
        "state": "filled",
    }
    event.pop("fee")
    event.pop("feeCcy")
    stream = FakeOrderStream([event])
    service = ExecutionFillService(
        client=FakeFillClient([]),
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
        private_order_stream=stream,
        enable_private_stream=True,
    )
    runner = DaemonRunner()
    runner.register(service)
    service.setup()

    service.tick()

    status = runner.runtime.get_value("execution.fills.status")
    allocation = position_store.get_allocation("ws-fill")
    assert allocation is not None
    assert allocation.fee == -0.03
    assert status["websocket_connected"] is True
    assert status["websocket_events_processed"] == 1
    assert status["websocket_fills_applied"] == 1
    assert position_store.get("ws-unit").opened_quantity == 0.1
    service.teardown()
    assert stream.stopped is True


def test_private_order_fill_is_idempotent_with_rest_catchup(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="same-unit",
            exchange_order_id="same-order",
            status="pending_open",
            metadata_json='{"expected_quantity":0.1,"order_action":"open"}',
        )
    )
    fill = _raw_fill("same-fill", "same-order")
    service = ExecutionFillService(
        client=FakeFillClient([fill]),
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
        private_order_stream=FakeOrderStream([fill]),
    )

    service.tick()

    assert position_store.get("same-unit").opened_quantity == 0.1
    assert len(position_store.list_allocations("same-unit")) == 1


def test_private_stream_drop_forces_rest_catchup_before_health_recovers(tmp_path):
    trade_store = TradeStore(str(tmp_path / "trades.db"))
    stream = FakeOrderStream()
    stream.started = True
    service = ExecutionFillService(
        client=FakeFillClient([]),
        allocator=ExecutionAllocationService(trade_store=trade_store),
        private_order_stream=stream,
        rest_poll_interval=60,
    )
    runner = DaemonRunner()
    runner.register(service)
    disabled = []
    service._disable_entries_for_execution_error = lambda: disabled.append(True)

    service.tick()
    stream.dropped_events = 1
    service.tick()
    dropped = runner.runtime.get_value("execution.fills.status")
    service.tick()
    recovered = runner.runtime.get_value("execution.fills.status")

    assert dropped["health_state"] == "degraded"
    assert dropped["websocket_drops_pending_catchup"] == 1
    assert disabled == [True]
    assert recovered["health_state"] == "healthy"
    assert recovered["websocket_drops_pending_catchup"] == 0
    assert recovered["last_health_failure_at"] == dropped["updated_at"]
    assert "await REST catch-up" in recovered["last_health_failure_reasons"][0]
    assert len(service.client.fill_calls) == 2


def test_private_reduce_fill_rearms_confirmed_remainder_in_same_tick(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(LogicalPositionRecord(
        id="ws-reduce", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=0.1, remaining_quantity=0.1, status="reducing",
        exchange_order_id="reduce-order", client_order_id="reduce-client",
        metadata_json=(
            '{"order_action":"reduce","execution_quantity":0.04}'
        ),
    ))
    position_store.link_execution_order(
        "ws-reduce", exchange_order_id="reduce-order",
        client_order_id="reduce-client", action="reduce",
    )
    position_store.save_protection(LogicalPositionProtection(
        position_id="ws-reduce", kind="standalone_stop", status="canceled",
        algo_id="old-algo", algo_client_order_id="old-client",
        quantity=0.1, stop_loss=1900,
    ))
    event = {
        **_raw_fill("ws-reduce-fill", "reduce-order", client_order_id="reduce-client"),
        "side": "sell", "fillSz": "0.04", "state": "filled",
    }
    rearm = FakeRearmProtection(position_store)
    service = ExecutionFillService(
        client=FakeFillClient([]),
        allocator=ExecutionAllocationService(trade_store, position_store),
        private_order_stream=FakeOrderStream([event]),
        enable_private_stream=True,
        rest_poll_interval=60,
        protection_service=rearm,
    )
    runner = DaemonRunner()
    runner.register(service)
    service.setup()

    service.tick()

    assert position_store.get("ws-reduce").remaining_quantity == 0.06
    assert position_store.get_protection("ws-reduce").status == "active"
    assert position_store.get_protection("ws-reduce").quantity == 0.06
    assert rearm.calls == ["ws-reduce"]
    status = runner.runtime.get_value("execution.fills.status")
    assert status["websocket_fills_applied"] == 1
    assert status["protection_rearmed"] == 1
    metadata = json.loads(position_store.get("ws-reduce").metadata_json)
    assert metadata["protection_gap_resolved_at"]
    assert metadata["protection_gap_seconds"] >= 0


def test_protection_gap_disables_entries_and_alerts_after_bound(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    started = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    position_store.save(LogicalPositionRecord(
        id="gap-unit", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=0.1, remaining_quantity=0.1, status="reducing",
        metadata_json=json.dumps({"protection_gap_started_at": started}),
    ))
    position_store.save_protection(LogicalPositionProtection(
        position_id="gap-unit", kind="standalone_stop", status="canceled",
        algo_id="old-algo", algo_client_order_id="old-client",
        quantity=0.1, stop_loss=1900,
    ))
    service = ExecutionFillService(
        client=FakeFillClient([]),
        allocator=ExecutionAllocationService(trade_store, position_store),
        protection_service=FakeRearmProtection(position_store),
        protection_gap_alert_seconds=5,
    )
    status = service._empty_status()

    service._track_protection_gaps(status)
    service._track_protection_gaps(status)

    assert status["unprotected_positions"] == 2
    assert status["oldest_protection_gap_seconds"] >= 10
    assert status["protection_gap_alerts"] == 1
    events = service.allocator.audit_store.list(
        event_type="position.protection_gap_exceeded",
        position_id="gap-unit",
    )
    assert len(events) == 1


def test_protective_stop_trigger_fill_allocates_to_owned_logical_unit(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="protected-unit",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            status="open",
        )
    )
    position_store.save_protection(
        LogicalPositionProtection(
            position_id="protected-unit",
            kind="attached_stop",
            algo_id="algo-stop-a",
            algo_client_order_id="algo-client-a",
            quantity=0.1,
            stop_loss=1900,
        )
    )
    fill = {**_raw_fill("stop-fill-a", "trigger-order-a"), "side": "sell"}
    service = ExecutionFillService(
        client=FakeFillClient(
            [fill],
            orders={
                "trigger-order-a": {
                    "ordId": "trigger-order-a",
                    "instId": "ETH-USDT-SWAP",
                    "algoId": "algo-stop-a",
                    "algoClOrdId": "algo-client-a",
                    "state": "filled",
                }
            },
        ),
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
    )

    service.tick()

    position = position_store.get("protected-unit")
    protection = position_store.get_protection("protected-unit")
    allocation = position_store.get_allocation("stop-fill-a")
    assert allocation.action == "close"
    assert allocation.exchange_order_id == "trigger-order-a"
    assert position.status == "closed"
    assert position.remaining_quantity == 0
    assert protection.status == "exhausted"
    assert protection.trigger_order_id == "trigger-order-a"


def test_canceled_software_close_rearms_protection_for_remaining_quantity(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="rearm-unit",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            status="closing",
            exchange_order_id="close-order-a",
            client_order_id="close-client-a",
            metadata_json='{"order_action":"close"}',
        )
    )
    position_store.save_protection(
        LogicalPositionProtection(
            position_id="rearm-unit",
            kind="standalone_stop",
            status="canceled",
            algo_id="algo-old",
            algo_client_order_id="algo-client-old",
            quantity=0.1,
            stop_loss=1900,
        )
    )
    rearm = FakeRearmProtection(position_store)
    client = FakeFillClient(
        [],
        orders={
            "close-order-a": {
                "ordId": "close-order-a",
                "state": "canceled",
                "instId": "ETH-USDT-SWAP",
            }
        },
    )
    service = ExecutionFillService(
        client=client,
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
        protection_service=rearm,
    )
    runner = DaemonRunner()
    runner.register(service)

    service.tick()

    status = runner.runtime.get_value("execution.fills.status")
    assert position_store.get("rearm-unit").status == "open"
    assert position_store.get_protection("rearm-unit").status == "active"
    assert rearm.calls == ["rearm-unit"]
    assert status["protection_rearmed"] == 1


def test_canceled_partial_reduce_recovers_confirmed_remainder_and_protection(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="reduce-unit",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            status="reducing",
            exchange_order_id="reduce-order-a",
            client_order_id="reduce-client-a",
            metadata_json=(
                '{"order_action":"reduce","execution_quantity":0.04}'
            ),
        )
    )
    position_store.link_execution_order(
        "reduce-unit",
        exchange_order_id="reduce-order-a",
        client_order_id="reduce-client-a",
        action="reduce",
    )
    position_store.save_protection(
        LogicalPositionProtection(
            position_id="reduce-unit",
            kind="standalone_stop",
            status="canceled",
            algo_id="algo-old",
            algo_client_order_id="algo-client-old",
            quantity=0.1,
            stop_loss=1900,
        )
    )
    allocator = ExecutionAllocationService(
        trade_store=trade_store,
        position_store=position_store,
    )
    partial = allocator.ingest(
        ConfirmedExecutionFill(
            fill_id="reduce-fill-a",
            exchange_order_id="reduce-order-a",
            quantity=0.02,
            price=2000,
            confirmation_source="okx_fill",
        )
    )
    assert partial.execution_status == "partially_reduced"

    rearm = FakeRearmProtection(position_store)
    service = ExecutionFillService(
        client=FakeFillClient(
            [],
            orders={
                "reduce-order-a": {
                    "ordId": "reduce-order-a",
                    "state": "canceled",
                    "instId": "ETH-USDT-SWAP",
                }
            },
        ),
        allocator=allocator,
        protection_service=rearm,
    )
    runner = DaemonRunner()
    runner.register(service)

    service.tick()

    recovered = position_store.get("reduce-unit")
    protection = position_store.get_protection("reduce-unit")
    assert recovered.status == "open"
    assert recovered.remaining_quantity == 0.08
    assert recovered.exchange_order_id == ""
    assert recovered.client_order_id == ""
    assert protection.status == "active"
    assert protection.quantity == 0.08
    assert rearm.calls == ["reduce-unit"]


def test_partial_stop_trigger_stays_tracked_and_rearms_if_child_is_canceled(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="partial-stop-unit",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            status="open",
        )
    )
    position_store.save_protection(
        LogicalPositionProtection(
            position_id="partial-stop-unit",
            kind="attached_stop",
            algo_id="algo-partial",
            algo_client_order_id="algo-client-partial",
            quantity=0.1,
            stop_loss=1900,
        )
    )
    fill = {
        **_raw_fill("partial-stop-fill", "trigger-partial"),
        "side": "sell",
        "fillSz": "0.04",
        "algoId": "algo-partial",
        "algoClOrdId": "algo-client-partial",
    }
    client = FakeFillClient(
        [fill],
        orders={
            "trigger-partial": {
                "ordId": "trigger-partial",
                "state": "partially_filled",
                "instId": "ETH-USDT-SWAP",
            }
        },
    )
    rearm = FakeRearmProtection(position_store)
    service = ExecutionFillService(
        client=client,
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
        protection_service=rearm,
    )

    service.tick()

    partial = position_store.get("partial-stop-unit")
    assert partial.status == "closing"
    assert partial.remaining_quantity == 0.06
    assert partial.exchange_order_id == "trigger-partial"
    assert position_store.get_protection(partial.id).status == "triggered"

    client.fills = []
    client.orders["trigger-partial"]["state"] = "canceled"
    service.tick()

    recovered = position_store.get("partial-stop-unit")
    assert recovered.status == "open"
    assert recovered.remaining_quantity == 0.06
    assert position_store.get_protection(recovered.id).status == "active"
    assert position_store.get_protection(recovered.id).quantity == 0.06
    assert rearm.calls == ["partial-stop-unit"]


def test_missing_close_after_restart_rearms_canceled_protection(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="restart-close-unit",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            status="closing",
            client_order_id="unknown-close-client",
            metadata_json=(
                '{"correlation_id":"unknown-close-client",'
                '"order_action":"close","close_quantity":0.1}'
            ),
        )
    )
    position_store.save_protection(
        LogicalPositionProtection(
            position_id="restart-close-unit",
            kind="attached_stop",
            status="canceled",
            algo_id="algo-canceled",
            algo_client_order_id="algo-client-canceled",
            quantity=0.1,
            stop_loss=1900,
        )
    )
    rearm = FakeRearmProtection(position_store)
    service = ExecutionFillService(
        client=FakeFillClient([]),
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
        stale_after_seconds=0,
        protection_service=rearm,
    )

    service.tick()

    recovered = position_store.get("restart-close-unit")
    assert recovered.status == "open"
    assert recovered.client_order_id == ""
    assert position_store.get_protection(recovered.id).status == "active"
    assert rearm.calls == ["restart-close-unit"]


def test_execution_fill_service_handles_partial_fill_cancel_and_restart_recovery(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    trade_store.save_trade(
        TradeRecord(
            id="entry-unit",
            status="pending_open",
            inst_id="ETH-USDT-SWAP",
            metadata_json='{"expected_quantity":0.1,"order_action":"open"}',
        )
    )
    position_store.save(
        LogicalPositionRecord(
            id="entry-unit",
            trade_id="entry-unit",
            inst_id="ETH-USDT-SWAP",
            status="pending_open",
            exchange_order_id="entry-order",
            client_order_id="entryclient1",
            opened_quantity=0.0,
            remaining_quantity=0.0,
            metadata_json='{"expected_quantity":0.1,"order_action":"open"}',
        )
    )
    old_time = int(
        (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp() * 1000
    )
    client = FakeFillClient(
        {
            "": [
                {
                    **_raw_fill("fill-a", "entry-order", "bill-a", "entryclient1"),
                    "fillSz": "0.04",
                }
            ],
            "bill-a": [],
        },
        orders={
            "entry-order": {
                "ordId": "entry-order",
                "state": "live",
                "instId": "ETH-USDT-SWAP",
                "uTime": str(old_time),
            }
        },
    )
    allocator = ExecutionAllocationService(
        trade_store=trade_store,
        position_store=position_store,
    )
    service = ExecutionFillService(
        client=client,
        allocator=allocator,
        stale_after_seconds=300,
    )
    runner = DaemonRunner()
    runner.register(service)

    service.tick()

    partial = position_store.get("entry-unit")
    status = runner.runtime.get_value("execution.fills.status")
    assert status["applied"] == 1
    assert status["stale_cancel_requested"] == 1
    assert partial.opened_quantity == 0.04
    assert partial.status == "open"
    assert partial.exchange_order_id == "entry-order"
    assert position_store.is_order_cancel_requested(
        "entry-unit", exchange_order_id="entry-order"
    )
    assert client.cancel_calls == [("ETH-USDT-SWAP", "entry-order")]

    client.orders["entry-order"] = {
        "ordId": "entry-order",
        "state": "canceled",
        "instId": "ETH-USDT-SWAP",
    }
    restart_service = ExecutionFillService(
        client=client,
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
        stale_after_seconds=300,
    )
    restart_runner = DaemonRunner()
    restart_runner.register(restart_service)

    restart_service.tick()

    recovered = position_store.get("entry-unit")
    restart_status = restart_runner.runtime.get_value("execution.fills.status")
    assert restart_status["terminal_recovered"] == 1
    assert recovered.status == "open"
    assert recovered.exchange_order_id == ""
    assert recovered.client_order_id == ""
    assert trade_store.get_trade("entry-unit").status == "open"


def test_private_order_event_recovers_terminal_order_without_waiting_for_rest(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="canceled-unit",
            inst_id="ETH-USDT-SWAP",
            exchange_order_id="canceled-order",
            status="pending_open",
        )
    )
    stream = FakeOrderStream(
        [{"ordId": "canceled-order", "state": "canceled", "instId": "ETH-USDT-SWAP"}]
    )
    service = ExecutionFillService(
        client=FakeFillClient([]),
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
        private_order_stream=stream,
        rest_poll_interval=60,
    )
    service._next_rest_poll = float("inf")

    service.tick()

    assert position_store.get("canceled-unit").status == "failed"


def test_execution_fill_service_applies_duplicates_and_reports_unmatched(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="unit-a",
            exchange_order_id="order-a",
            status="pending_open",
            opened_quantity=0.0,
            remaining_quantity=0.0,
            metadata_json='{"expected_quantity":0.1,"order_action":"open"}',
        )
    )
    client = FakeFillClient(
        [
            _raw_fill(),
            _raw_fill(),
            _raw_fill("fill-unknown", "order-unknown"),
            {"billId": "bill-invalid"},
        ]
    )
    service = ExecutionFillService(
        client=client,
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
    )
    runner = DaemonRunner()
    runner.register(service)

    service.tick()

    status = runner.runtime.get_value("execution.fills.status")
    assert status["fetched"] == 4
    assert status["applied"] == 1
    assert status["idempotent"] == 1
    assert status["unmatched"] == 1
    assert status["invalid"] == 1
    assert position_store.get("unit-a").opened_quantity == 0.1
    rejected = service.allocator.audit_store.list(event_type="execution.fill_rejected")
    assert len(rejected) == 1
    assert rejected[0].payload["bill_id"] == "bill-invalid"


def test_execution_cursor_store_persists_partial_and_committed_state(tmp_path):
    store = ExecutionCursorStore(str(tmp_path / "trades.db"))

    store.checkpoint(
        "okx:fills-history:SWAP",
        pending_high_water_id="bill-300",
        next_after_id="bill-201",
    )
    partial = ExecutionCursorStore(store.db_path).get("okx:fills-history:SWAP")
    completed = store.complete(
        "okx:fills-history:SWAP",
        high_water_id="bill-300",
    )

    assert store.applied_schema_versions() == [1]
    assert partial.in_progress is True
    assert partial.high_water_id == ""
    assert partial.next_after_id == "bill-201"
    assert completed.high_water_id == "bill-300"
    assert completed.in_progress is False


def test_execution_fill_service_continues_bounded_catchup_across_ticks(tmp_path):
    pages = {
        "": [
            _raw_fill(f"fill-{bill}", "unknown", str(bill))
            for bill in range(300, 200, -1)
        ],
        "201": [
            _raw_fill(f"fill-{bill}", "unknown", str(bill))
            for bill in range(200, 100, -1)
        ],
        "101": [
            _raw_fill(f"fill-{bill}", "unknown", str(bill))
            for bill in range(100, 50, -1)
        ],
    }
    trade_store = TradeStore(str(tmp_path / "trades.db"))
    cursor_store = ExecutionCursorStore(trade_store.db_path)
    client = FakeFillClient(pages)
    service = ExecutionFillService(
        client=client,
        allocator=ExecutionAllocationService(trade_store=trade_store),
        cursor_store=cursor_store,
        max_pages_per_tick=2,
    )
    runner = DaemonRunner()
    runner.register(service)

    service.tick()
    partial = cursor_store.get(service.FILL_STREAM_ID)
    first_status = runner.runtime.get_value("execution.fills.status")
    service.tick()
    completed = cursor_store.get(service.FILL_STREAM_ID)
    second_status = runner.runtime.get_value("execution.fills.status")

    assert client.fill_calls == ["", "201", "101"]
    assert partial.high_water_id == ""
    assert partial.pending_high_water_id == "300"
    assert partial.next_after_id == "101"
    assert first_status["caught_up"] is False
    assert first_status["cursor_in_progress"] is True
    assert completed.high_water_id == "300"
    assert completed.in_progress is False
    assert second_status["caught_up"] is True
    assert second_status["history_exhausted"] is True


def test_execution_fill_service_keeps_page_checkpoint_after_later_failure(tmp_path):
    first_page = [
        _raw_fill(f"fill-{bill}", "unknown", str(bill))
        for bill in range(300, 200, -1)
    ]
    trade_store = TradeStore(str(tmp_path / "trades.db"))
    cursor_store = ExecutionCursorStore(trade_store.db_path)
    client = FakeFillClient({"": first_page, "201": RuntimeError("OKX unavailable")})
    service = ExecutionFillService(
        client=client,
        allocator=ExecutionAllocationService(trade_store=trade_store),
        cursor_store=cursor_store,
        max_pages_per_tick=2,
    )
    runner = DaemonRunner()
    runner.register(service)

    with pytest.raises(RuntimeError, match="OKX unavailable"):
        service.tick()

    cursor = cursor_store.get(service.FILL_STREAM_ID)
    status = runner.runtime.get_value("execution.fills.status")
    assert cursor.high_water_id == ""
    assert cursor.pending_high_water_id == "300"
    assert cursor.next_after_id == "201"
    assert status["cursor_errors"] == 1
    assert status["cursor_in_progress"] is True


def test_execution_fill_service_does_not_advance_page_without_bill_ids(tmp_path):
    trade_store = TradeStore(str(tmp_path / "trades.db"))
    cursor_store = ExecutionCursorStore(trade_store.db_path)
    service = ExecutionFillService(
        client=FakeFillClient([{"tradeId": "fill-a"}]),
        allocator=ExecutionAllocationService(trade_store=trade_store),
        cursor_store=cursor_store,
    )

    with pytest.raises(ValueError, match="missing billId"):
        service.tick()

    assert cursor_store.get(service.FILL_STREAM_ID).in_progress is False
    assert cursor_store.get(service.FILL_STREAM_ID).high_water_id == ""


def test_execution_fill_service_recovers_canceled_entry_and_close_orders(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    trade_store.save_trade(
        TradeRecord(
            id="entry-unit",
            status="pending_open",
            inst_id="ETH-USDT-SWAP",
        )
    )
    position_store.save(
        LogicalPositionRecord(
            id="entry-unit",
            trade_id="entry-unit",
            inst_id="ETH-USDT-SWAP",
            status="pending_open",
            exchange_order_id="entry-order",
            opened_quantity=0.0,
            remaining_quantity=0.0,
        )
    )
    position_store.save(
        LogicalPositionRecord(
            id="close-unit",
            inst_id="BTC-USDT-SWAP",
            status="closing",
            exchange_order_id="close-order",
            opened_quantity=0.1,
            remaining_quantity=0.06,
        )
    )
    client = FakeFillClient(
        [],
        orders={
            "entry-order": {"state": "canceled"},
            "close-order": {"state": "canceled"},
        },
    )
    service = ExecutionFillService(
        client=client,
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
    )
    runner = DaemonRunner()
    runner.register(service)

    service.tick()

    status = runner.runtime.get_value("execution.fills.status")
    assert status["orders_checked"] == 2
    assert status["terminal_recovered"] == 2
    assert position_store.get("entry-unit").status == "failed"
    assert trade_store.get_trade("entry-unit").status == "failed"
    assert position_store.get("close-unit").status == "open"
    assert position_store.get("close-unit").remaining_quantity == 0.06
    assert position_store.get("close-unit").exchange_order_id == ""


def test_execution_fill_service_links_order_recovered_by_client_id(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="entry-unit",
            inst_id="ETH-USDT-SWAP",
            status="pending_open",
            client_order_id="entryclient1",
        )
    )
    client = FakeFillClient(
        [],
        orders={"entryclient1": {"ordId": "entry-order", "state": "live"}},
    )
    service = ExecutionFillService(
        client=client,
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
    )
    runner = DaemonRunner()
    runner.register(service)

    service.tick()

    position = position_store.get("entry-unit")
    assert position.exchange_order_id == "entry-order"
    assert position.client_order_id == "entryclient1"
    assert runner.runtime.get_value("execution.fills.status")["client_orders_linked"] == 1


def test_execution_fill_service_recovers_stale_client_intent_missing_from_okx(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    trade_store.save_trade(TradeRecord(id="entry-unit", status="pending_open"))
    position_store.save(
        LogicalPositionRecord(
            id="entry-unit",
            trade_id="entry-unit",
            inst_id="ETH-USDT-SWAP",
            status="pending_open",
            client_order_id="entryclient1",
        )
    )
    service = ExecutionFillService(
        client=FakeFillClient([]),
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
        stale_after_seconds=0,
    )
    runner = DaemonRunner()
    runner.register(service)

    service.tick()

    position = position_store.get("entry-unit")
    assert position.status == "failed"
    assert position.client_order_id == ""
    assert trade_store.get_trade("entry-unit").status == "failed"
    assert (
        runner.runtime.get_value("execution.fills.status")[
            "missing_client_orders_recovered"
        ]
        == 1
    )


def test_execution_fill_service_requests_stale_cancel_once(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="pending-unit",
            inst_id="ETH-USDT-SWAP",
            status="pending_open",
            exchange_order_id="stale-order",
            opened_quantity=0.0,
            remaining_quantity=0.0,
        )
    )
    old_time = int(
        (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp() * 1000
    )
    client = FakeFillClient(
        [],
        orders={"stale-order": {"state": "live", "uTime": str(old_time)}},
    )
    service = ExecutionFillService(
        client=client,
        allocator=ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
        ),
        stale_after_seconds=300,
    )

    service.tick()
    service.tick()

    assert client.cancel_calls == [("ETH-USDT-SWAP", "stale-order")]
    assert position_store.get("pending-unit").status == "pending_open"


def test_filled_order_without_fill_details_alerts_once_after_repeated_polls(tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="missing-fill-unit",
            inst_id="ETH-USDT-SWAP",
            status="pending_open",
            exchange_order_id="filled-order",
            opened_quantity=0.0,
            remaining_quantity=0.0,
        )
    )
    client = FakeFillClient([], orders={"filled-order": {"state": "filled"}})
    allocator = ExecutionAllocationService(
        trade_store=trade_store,
        position_store=position_store,
    )
    service = ExecutionFillService(
        client=client,
        allocator=allocator,
        missing_fill_alert_after=3,
    )
    runner = DaemonRunner()
    runner.register(service)

    service.tick()
    service.tick()
    service.tick()
    third_status = runner.runtime.get_value("execution.fills.status")
    service.tick()

    metadata = json.loads(position_store.get("missing-fill-unit").metadata_json)
    alerts = allocator.audit_store.list(
        event_type="position.filled_without_allocation",
        position_id="missing-fill-unit",
    )
    runtime_alerts = runner.runtime.events.recent(
        event_type="execution.filled_without_allocation"
    )
    assert third_status["missing_fill_alerts"] == 1
    assert metadata["filled_without_allocation"]["count"] == 4
    assert metadata["filled_without_allocation"]["alerted_at"]
    assert len(alerts) == 1
    assert len(runtime_alerts) == 1


def test_fill_service_applies_pending_confirmed_entry_stop_materialization(tmp_path):
    db_path = str(tmp_path / "fills.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(LogicalPositionRecord(
        id="relative", source="strategy", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=1, remaining_quantity=1, entry_price=102, status="open",
        metadata_json=json.dumps({
            "requires_manual_review": True,
            "protection_materialization_pending": True,
        }),
    ))
    condition = position_store.create_close_condition(
        id="relative-stop", position_id="relative", purpose="stop_loss",
        expression={"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 95},
        metadata={"pending_materialization": {
            "status": "pending_exchange_amend",
            "expression": {"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 96.9},
            "metadata": {"materialization": {"basis": "confirmed_average_entry"}},
            "entry_fill_id": "fill-1",
        }},
    )
    position_store.save_protection(LogicalPositionProtection(
        position_id="relative", kind="attached_stop", status="active",
        algo_id="algo", algo_client_order_id="algo-client", quantity=1, stop_loss=95,
    ))

    class FakeProtectionService:
        def __init__(self):
            self.calls = []

        def amend_stop_condition(self, position_id, condition_id, **kwargs):
            self.calls.append((position_id, condition_id, kwargs))
            return position_store.get(position_id)

    protection = FakeProtectionService()
    service = ExecutionFillService(
        allocator=ExecutionAllocationService(trade_store, position_store),
        protection_service=protection,
        allow_order_mutations=True,
    )
    status = service._empty_status()

    service._apply_pending_rule_materializations("relative", status)

    assert status["rule_materializations"] == 1
    assert protection.calls[0][2]["expression"]["value"] == 96.9
    assert protection.calls[0][2]["expected_condition_updated_at"] == condition.updated_at
    metadata = json.loads(position_store.get("relative").metadata_json)
    assert metadata["protection_materialization_pending"] is False
