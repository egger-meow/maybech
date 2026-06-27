from src.trading.executor import Executor


class FakeClient:
    def __init__(self):
        self.entry = None
        self.close = None

    def get_instruments(self, **kwargs):
        return [{
            "instId": kwargs["inst_id"],
            "state": "live",
            "minSz": "0.1",
            "lotSz": "0.1",
            "tickSz": "0.01",
        }]

    def place_limit_order(self, **kwargs):
        self.entry = kwargs
        return {"ordId": "entry-a"}

    def place_reduce_market_order(self, **kwargs):
        self.close = kwargs
        return {"ordId": "close-a"}


def test_live_entry_rejects_missing_contract_size():
    client = FakeClient()
    executor = Executor(client, dry_run=False)

    assert executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2000,
        requested_size="",
        stop_loss_price=1900,
        client_order_id="entryclient1",
    ) == {}
    assert client.entry is None


def test_live_entry_normalizes_size_and_price_from_okx_metadata():
    client = FakeClient()
    executor = Executor(client, dry_run=False)

    result = executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2000.126,
        requested_size="0.3",
        stop_loss_price=1900.124,
        client_order_id="entryclient2",
        take_profit_price=2200.125,
    )

    assert result == {"ordId": "entry-a", "maybechRequestedSize": "0.3"}
    assert client.entry["side"] == "buy"
    assert client.entry["sz"] == "0.3"
    assert client.entry["px"] == "2000.13"
    assert client.entry["sl_trigger_px"] == "1900.12"
    assert client.entry["sl_ord_px"] == "-1"
    assert client.entry["tp_trigger_px"] == "2200.13"
    assert client.entry["tp_ord_px"] == "-1"
    assert client.entry["client_order_id"] == "entryclient2"


def test_live_close_rejects_quantity_outside_lot_precision():
    client = FakeClient()
    executor = Executor(client, dry_run=False)

    assert executor.close_position(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        quantity=0.15,
        client_order_id="closeclient1",
    ) == {}
    assert client.close is None


def test_entry_rejects_stop_on_wrong_side_before_submission():
    client = FakeClient()
    executor = Executor(client, dry_run=False)

    assert executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2000,
        requested_size="1",
        stop_loss_price=2100,
        client_order_id="entryclient3",
    ) == {}
    assert client.entry is None
