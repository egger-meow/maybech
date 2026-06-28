from decimal import Decimal

from src.trading.account_risk import AccountRiskLimits, AccountRiskStore
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
            "ctVal": "0.01",
            "ctType": "linear",
            "settleCcy": "USDT",
        }]

    def get_leverage(self, **kwargs):
        return [{"instId": kwargs["inst_id"], "mgnMode": "cross", "lever": "3"}]

    def get_positions(self, **kwargs):
        return []

    def get_pending_orders(self, **kwargs):
        return []

    def place_limit_order(self, **kwargs):
        self.entry = kwargs
        return {"ordId": "entry-a"}

    def place_reduce_market_order(self, **kwargs):
        self.close = kwargs
        return {"ordId": "close-a"}


def _live_executor(client, tmp_path):
    store = AccountRiskStore(str(tmp_path / "trades.db"))
    store.save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=Decimal("1000"),
            max_total_exposure_usd=Decimal("10000"),
            max_leverage=Decimal("5"),
        )
    )
    store.set_entries_enabled(True)
    return Executor(client, dry_run=False, risk_store=store)


def test_live_entry_rejects_missing_contract_size(tmp_path):
    client = FakeClient()
    executor = _live_executor(client, tmp_path)

    assert executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2000,
        requested_size="",
        stop_loss_price=1900,
        client_order_id="entryclient1",
    ) == {}
    assert client.entry is None


def test_live_entry_normalizes_size_and_price_from_okx_metadata(tmp_path):
    client = FakeClient()
    executor = _live_executor(client, tmp_path)
    approval = executor.approve_entry(
        inst_id="ETH-USDT-SWAP",
        requested_size="0.3",
        entry_price=2000.126,
    )

    result = executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2000.126,
        requested_size="0.3",
        stop_loss_price=1900.124,
        client_order_id="entryclient2",
        take_profit_price=2200.125,
        risk_approval=approval,
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


def test_live_close_rejects_quantity_outside_lot_precision(tmp_path):
    client = FakeClient()
    executor = _live_executor(client, tmp_path)

    assert executor.close_position(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        quantity=0.15,
        client_order_id="closeclient1",
    ) == {}
    assert client.close is None


def test_entry_rejects_stop_on_wrong_side_before_submission(tmp_path):
    client = FakeClient()
    executor = _live_executor(client, tmp_path)

    assert executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2000,
        requested_size="1",
        stop_loss_price=2100,
        client_order_id="entryclient3",
    ) == {}
    assert client.entry is None


def test_live_entry_rejects_missing_or_mismatched_risk_approval(tmp_path):
    client = FakeClient()
    executor = _live_executor(client, tmp_path)
    approval = executor.approve_entry(
        inst_id="ETH-USDT-SWAP",
        requested_size="0.3",
        entry_price=2000,
    )

    assert executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2001,
        requested_size="0.3",
        stop_loss_price=1900,
        client_order_id="entryclient4",
        risk_approval=approval,
    ) == {}
    assert client.entry is None


def test_live_entry_risk_approval_is_single_use(tmp_path):
    client = FakeClient()
    executor = _live_executor(client, tmp_path)
    approval = executor.approve_entry(
        inst_id="ETH-USDT-SWAP",
        requested_size="0.3",
        entry_price=2000,
    )
    payload = {
        "inst_id": "ETH-USDT-SWAP",
        "position_side": "long",
        "entry_price": 2000,
        "requested_size": "0.3",
        "stop_loss_price": 1900,
        "client_order_id": "entryclient5",
        "risk_approval": approval,
    }

    assert executor.execute(**payload)["ordId"] == "entry-a"
    assert executor.execute(**payload) == {}


def test_live_entry_rejects_approval_from_another_executor(tmp_path):
    client = FakeClient()
    executor = _live_executor(client, tmp_path)
    other = _live_executor(client, tmp_path)
    approval = other.approve_entry(
        inst_id="ETH-USDT-SWAP",
        requested_size="0.3",
        entry_price=2000,
    )

    assert executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2000,
        requested_size="0.3",
        stop_loss_price=1900,
        client_order_id="entryclient6",
        risk_approval=approval,
    ) == {}
    assert client.entry is None
