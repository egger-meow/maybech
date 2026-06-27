from src.daemon.execution_fill_service import ExecutionFillService
from src.daemon.service import DaemonRunner
from src.exchange.fills import normalize_okx_fill
from src.trading.execution_allocation import ExecutionAllocationService
from src.trading.logical_position_store import LogicalPositionRecord, LogicalPositionStore
from src.trading.trade_store import TradeStore


class FakeFillClient:
    def __init__(self, fills):
        self.fills = fills

    def get_fills(self, inst_type="SWAP", limit="100"):
        assert inst_type == "SWAP"
        assert limit == "100"
        return self.fills


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
