from decimal import Decimal

from src.trading.account_risk import AccountRiskLimits, AccountRiskStore
from src.trading.executor import Executor


class FakeClient:
    def __init__(self):
        self.entry = None
        self.close = None
        self.cancelled = []
        self.protection_failure = False
        self.order_state = "filled"

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

    def get_balance(self):
        return [{"totalEq": "1000"}]

    def place_limit_order(self, **kwargs):
        self.entry = kwargs
        return {"ordId": "entry-a"}

    def get_order(self, inst_id, order_id="", client_order_id=""):
        attachment_request = self.entry["attach_algo_client_order_id"]
        attachment = {
            "slTriggerPx": self.entry["sl_trigger_px"],
            "slOrdPx": self.entry["sl_ord_px"],
            "tpTriggerPx": self.entry["tp_trigger_px"],
            "tpOrdPx": self.entry["tp_ord_px"],
            "attachAlgoClOrdId": attachment_request,
            "failCode": "51020" if self.protection_failure else "",
        }
        return [{
            "ordId": order_id,
            "clOrdId": self.entry["client_order_id"],
            "state": self.order_state,
            "ordType": "fok",
            "accFillSz": self.entry["sz"],
            "attachAlgoOrds": [attachment],
        }]

    def get_pending_algo_orders(self, *, inst_id, ord_type):
        expected_type = "oco" if self.entry["tp_trigger_px"] else "conditional"
        if ord_type != expected_type:
            return []
        return [{
            "algoId": "attached-a",
            "algoClOrdId": self.entry["attach_algo_client_order_id"],
            "instId": inst_id,
            "state": "live",
            "sz": self.entry["sz"],
            "slTriggerPx": self.entry["sl_trigger_px"],
            "slOrdPx": self.entry["sl_ord_px"],
            "tpTriggerPx": self.entry["tp_trigger_px"],
            "tpOrdPx": self.entry["tp_ord_px"],
        }]

    def cancel_order(self, inst_id, order_id):
        self.cancelled.append((inst_id, order_id))
        return {"ordId": order_id, "sCode": "0"}

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
            max_stop_loss_equity_pct=Decimal("10"),
            allowed_instruments=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
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
        side="long",
        requested_size="0.3",
        entry_price=2000.126,
        stop_loss_price=1900.124,
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

    assert result["ordId"] == "entry-a"
    assert result["maybechRequestedSize"] == "0.3"
    assert result["maybechProtectionVerified"] is True
    assert client.entry["side"] == "buy"
    assert client.entry["sz"] == "0.3"
    assert client.entry["px"] == "2000.12"
    assert client.entry["sl_trigger_px"] == "1900.12"
    assert client.entry["sl_ord_px"] == "-1"
    assert client.entry["tp_trigger_px"] == "2200.13"
    assert client.entry["tp_ord_px"] == "-1"
    assert client.entry["client_order_id"] == "entryclient2"
    assert client.entry["order_type"] == "fok"
    assert client.entry["attach_algo_client_order_id"].startswith("mba")
    assert result["maybechProtection"]["active"]["algo_id"] == "attached-a"


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
        side="long",
        requested_size="0.3",
        entry_price=2000,
        stop_loss_price=1900,
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


def test_live_entry_rejects_stop_loss_changed_after_approval(tmp_path):
    client = FakeClient()
    executor = _live_executor(client, tmp_path)
    approval = executor.approve_entry(
        inst_id="ETH-USDT-SWAP",
        side="long",
        requested_size="0.3",
        entry_price=2000,
        stop_loss_price=1900,
    )

    assert executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2000,
        requested_size="0.3",
        stop_loss_price=1800,
        client_order_id="entryclient9",
        risk_approval=approval,
    ) == {}
    assert client.entry is None


def test_live_entry_risk_approval_is_single_use(tmp_path):
    client = FakeClient()
    executor = _live_executor(client, tmp_path)
    approval = executor.approve_entry(
        inst_id="ETH-USDT-SWAP",
        side="long",
        requested_size="0.3",
        entry_price=2000,
        stop_loss_price=1900,
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
        side="long",
        requested_size="0.3",
        entry_price=2000,
        stop_loss_price=1900,
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


def test_live_filled_entry_kills_future_entries_when_active_protection_fails(tmp_path):
    client = FakeClient()
    client.protection_failure = True
    executor = _live_executor(client, tmp_path)
    approval = executor.approve_entry(
        inst_id="ETH-USDT-SWAP",
        side="long",
        requested_size="0.3",
        entry_price=2000,
        stop_loss_price=1900,
    )

    result = executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2000,
        requested_size="0.3",
        stop_loss_price=1900,
        client_order_id="entryclient7",
        risk_approval=approval,
    )

    assert result["ordId"] == "entry-a"
    assert result["maybechProtectionVerified"] is False
    assert result["maybechCancelRequested"] is False
    assert result["maybechEntryKillActivated"] is True
    assert result["maybechEmergencyCloseRequired"] is True
    assert result["maybechEmergencyCloseClientOrderId"].startswith("mbe")
    assert result["maybechEmergencyCloseQuantity"] == "0.3"
    assert executor.risk_store.entries_enabled() is False
    assert client.cancelled == []


def test_fok_partial_fill_cancels_remainder_and_kills_future_entries(tmp_path):
    client = FakeClient()
    client.order_state = "partially_filled"
    executor = _live_executor(client, tmp_path)
    approval = executor.approve_entry(
        inst_id="ETH-USDT-SWAP",
        side="long",
        requested_size="0.3",
        entry_price=2000,
        stop_loss_price=1900,
    )

    result = executor.execute(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        entry_price=2000,
        requested_size="0.3",
        stop_loss_price=1900,
        client_order_id="entryclient8",
        risk_approval=approval,
    )

    assert result["maybechProtectionVerified"] is False
    assert result["maybechOrderState"] == "partially_filled"
    assert result["maybechCancelRequested"] is True
    assert result["maybechEntryKillActivated"] is True
    assert client.cancelled == [("ETH-USDT-SWAP", "entry-a")]
