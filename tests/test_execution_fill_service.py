import json
from datetime import datetime, timedelta, timezone

import pytest

from src.daemon.execution_fill_service import ExecutionFillService
from src.daemon.service import DaemonRunner
from src.exchange.fills import normalize_okx_fill
from src.trading.execution_allocation import ExecutionAllocationService
from src.trading.execution_cursor_store import ExecutionCursorStore
from src.trading.logical_position_store import LogicalPositionRecord, LogicalPositionStore
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
    fill = normalize_okx_fill(_raw_fill())

    assert fill.fill_id == "fill-a"
    assert fill.exchange_order_id == "order-a"
    assert fill.quantity == 0.1
    assert fill.price == 2000.5
    assert fill.fee == -0.02
    assert fill.occurred_at.startswith("2026-")
    assert fill.metadata["inst_id"] == "ETH-USDT-SWAP"


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
