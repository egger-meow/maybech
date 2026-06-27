import json
from datetime import datetime, timedelta, timezone

from src.daemon.execution_fill_service import ExecutionFillService
from src.daemon.service import DaemonRunner
from src.exchange.fills import normalize_okx_fill
from src.trading.execution_allocation import ExecutionAllocationService
from src.trading.logical_position_store import LogicalPositionRecord, LogicalPositionStore
from src.trading.trade_store import TradeRecord, TradeStore


class FakeFillClient:
    def __init__(self, fills, orders=None):
        self.fills = fills
        self.orders = orders or {}
        self.cancel_calls = []

    def get_fills(self, inst_type="SWAP", limit="100"):
        assert inst_type == "SWAP"
        assert limit == "100"
        return self.fills

    def get_order(self, inst_id, order_id):
        order = self.orders.get(order_id)
        return [] if order is None else [order]

    def cancel_order(self, inst_id, order_id):
        self.cancel_calls.append((inst_id, order_id))
        return {"ordId": order_id, "sCode": "0"}


def _raw_fill(fill_id="fill-a", order_id="order-a"):
    return {
        "tradeId": fill_id,
        "ordId": order_id,
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
        [_raw_fill(), _raw_fill(), _raw_fill("fill-unknown", "order-unknown"), {}]
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
