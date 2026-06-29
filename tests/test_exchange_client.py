import pytest

import src.exchange.client as client_module
from src.exchange.client import OKXClient


class FakeTradeApi:
    def __init__(self):
        self.kwargs = None

    def get_fills_history(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"billId": "bill-a"}]}

    def get_fills(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"tradeId": "fill-a"}]}

    def place_order(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"ordId": "close-order", "sCode": "0", "sMsg": ""}]}

    def cancel_order(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"ordId": kwargs["ordId"], "sCode": "0"}]}

    def get_order(self, **kwargs):
        self.kwargs = kwargs
        return {
            "code": "0",
            "data": [{"ordId": kwargs.get("ordId", "recovered-order"), "state": "live"}],
        }

    def get_order_list(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"ordId": "pending-a"}]}

    def order_algos_list(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"algoId": "algo-a", "state": "live"}]}

    def place_algo_order(self, **kwargs):
        self.kwargs = kwargs
        return {
            "code": "0",
            "data": [{"algoId": "algo-a", "sCode": "0", "sMsg": ""}],
        }

    def amend_algo_order(self, **kwargs):
        self.kwargs = kwargs
        return {
            "code": "0",
            "data": [{"algoId": kwargs["algoId"], "sCode": "0", "sMsg": ""}],
        }

    def cancel_algo_order(self, params):
        self.kwargs = params
        return {
            "code": "0",
            "data": [{"algoId": params[0]["algoId"], "sCode": "0", "sMsg": ""}],
        }


class FakeAccountApi:
    def __init__(self):
        self.kwargs = None

    def get_leverage(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"lever": "5"}]}


class FakePublicApi:
    def __init__(self):
        self.kwargs = None

    def get_instruments(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"instId": kwargs["instId"]}]}


def test_okx_client_get_fills_history_paginates_by_bill_id():
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()

    fills = client.get_fills_history(
        inst_type="SWAP",
        limit="100",
        after="bill-a",
    )

    assert fills == [{"billId": "bill-a"}]
    assert client.trade_api.kwargs == {
        "instType": "SWAP",
        "limit": "100",
        "after": "bill-a",
    }


def test_okx_client_gets_recent_fills_for_one_order():
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()

    fills = client.get_fills(
        inst_type="SWAP",
        inst_id="BTC-USDT-SWAP",
        order_id="order-a",
        limit="100",
    )

    assert fills == [{"tradeId": "fill-a"}]
    assert client.trade_api.kwargs == {
        "instType": "SWAP",
        "instId": "BTC-USDT-SWAP",
        "ordId": "order-a",
        "limit": "100",
    }


def test_okx_client_preserves_per_order_error_details():
    with pytest.raises(RuntimeError, match="sCode=51008: Insufficient balance"):
        client_module._extract(
            {
                "code": "1",
                "msg": "All operations failed",
                "data": [{"sCode": "51008", "sMsg": "Insufficient balance"}],
            },
            label="place_limit_order",
        )


def test_okx_client_places_guarded_reduce_only_close(monkeypatch):
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()
    monkeypatch.setattr(client_module, "_ORDER_PLACEMENT_ARMED", True)

    result = client.place_reduce_market_order(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        sz="0.1",
        client_order_id="closeclient1",
        confirm=True,
    )

    assert result["ordId"] == "close-order"
    assert result["sCode"] == "0"
    assert client.trade_api.kwargs == {
        "instId": "ETH-USDT-SWAP",
        "tdMode": "cross",
        "side": "sell",
        "ordType": "market",
        "clOrdId": "closeclient1",
        "sz": "0.1",
        "posSide": "",
        "reduceOnly": "true",
    }


def test_entry_kill_blocks_entries_without_blocking_reduce_only_close(monkeypatch):
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()
    monkeypatch.setattr(client_module, "_ORDER_PLACEMENT_ARMED", True)
    monkeypatch.setattr(client_module, "_ENTRY_ORDER_PLACEMENT_ENABLED", False)

    with pytest.raises(PermissionError, match="entry order placement is disabled"):
        client.place_limit_order(
            inst_id="ETH-USDT-SWAP",
            side="buy",
            sz="1",
            px="2000",
            client_order_id="entryclient1",
            confirm=True,
        )

    result = client.place_reduce_market_order(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        sz="1",
        client_order_id="closeclient2",
        confirm=True,
    )
    assert result["ordId"] == "close-order"


def test_okx_client_rejects_nonzero_per_order_status(monkeypatch):
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()
    client.trade_api.place_order = lambda **kwargs: {
        "code": "0",
        "data": [{"ordId": "rejected-order", "sCode": "51008", "sMsg": "insufficient balance"}],
    }
    monkeypatch.setattr(client_module, "_ORDER_PLACEMENT_ARMED", True)
    monkeypatch.setattr(client_module, "_ENTRY_ORDER_PLACEMENT_ENABLED", True)

    with pytest.raises(RuntimeError, match="sCode=51008"):
        client.place_limit_order(
            inst_id="ETH-USDT-SWAP",
            side="buy",
            sz="1",
            px="2000",
            sl_trigger_px="1900",
            sl_ord_px="-1",
            client_order_id="entryclient2",
            attach_algo_client_order_id="attachclient2",
            confirm=True,
        )


def test_okx_client_sends_fok_entry_protection_through_sdk_attachment(monkeypatch):
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()
    monkeypatch.setattr(client_module, "_ORDER_PLACEMENT_ARMED", True)
    monkeypatch.setattr(client_module, "_ENTRY_ORDER_PLACEMENT_ENABLED", True)

    client.place_limit_order(
        inst_id="ETH-USDT-SWAP",
        side="buy",
        sz="1",
        px="2010",
        sl_trigger_px="1900",
        sl_ord_px="-1",
        tp_trigger_px="2200",
        tp_ord_px="-1",
        client_order_id="entryclient3",
        attach_algo_client_order_id="attachclient3",
        order_type="fok",
        confirm=True,
    )

    assert client.trade_api.kwargs == {
        "instId": "ETH-USDT-SWAP",
        "tdMode": "cross",
        "side": "buy",
        "ordType": "fok",
        "clOrdId": "entryclient3",
        "sz": "1",
        "px": "2010",
        "stpMode": "cancel_taker",
        "attachAlgoOrds": [{
            "attachAlgoClOrdId": "attachclient3",
            "tpTriggerPx": "2200",
            "tpOrdPx": "-1",
            "tpTriggerPxType": "last",
            "slTriggerPx": "1900",
            "slOrdPx": "-1",
            "slTriggerPxType": "last",
        }],
    }


def test_okx_client_get_order_uses_instrument_and_order_id():
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()

    orders = client.get_order("ETH-USDT-SWAP", "order-a")

    assert orders[0]["state"] == "live"
    assert client.trade_api.kwargs == {
        "instId": "ETH-USDT-SWAP",
        "ordId": "order-a",
    }


def test_okx_client_get_order_can_recover_by_client_order_id():
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()

    orders = client.get_order("ETH-USDT-SWAP", client_order_id="entryclient1")

    assert orders[0]["ordId"] == "recovered-order"
    assert client.trade_api.kwargs == {
        "instId": "ETH-USDT-SWAP",
        "clOrdId": "entryclient1",
    }


def test_okx_client_get_instruments_uses_public_endpoint():
    client = object.__new__(OKXClient)
    client.public_api = FakePublicApi()

    instruments = client.get_instruments(inst_type="SWAP", inst_id="ETH-USDT-SWAP")

    assert instruments == [{"instId": "ETH-USDT-SWAP"}]
    assert client.public_api.kwargs == {
        "instType": "SWAP",
        "instId": "ETH-USDT-SWAP",
    }


def test_okx_client_fetches_pending_orders_and_leverage():
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()
    client.account_api = FakeAccountApi()

    assert client.get_pending_orders("SWAP") == [{"ordId": "pending-a"}]
    assert client.trade_api.kwargs == {"instType": "SWAP"}
    assert client.get_leverage("ETH-USDT-SWAP", "cross") == [{"lever": "5"}]
    assert client.account_api.kwargs == {
        "instId": "ETH-USDT-SWAP",
        "mgnMode": "cross",
    }


def test_okx_client_places_and_lists_guarded_position_stop(monkeypatch):
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()
    monkeypatch.setattr(client_module, "_ORDER_PLACEMENT_ARMED", True)

    result = client.place_position_stop(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        sz="2",
        stop_trigger_px="2900",
        algo_client_order_id="protectionclient1",
        confirm=True,
    )

    assert result["algoId"] == "algo-a"
    assert client.trade_api.kwargs == {
        "instId": "ETH-USDT-SWAP",
        "tdMode": "cross",
        "side": "sell",
        "ordType": "conditional",
        "sz": "2",
        "posSide": "net",
        "reduceOnly": "true",
        "slTriggerPx": "2900",
        "slOrdPx": "-1",
        "slTriggerPxType": "last",
        "algoClOrdId": "protectionclient1",
    }

    assert client.get_pending_algo_orders(inst_id="ETH-USDT-SWAP") == [
        {"algoId": "algo-a", "state": "live"}
    ]

    amended = client.amend_position_stop(
        inst_id="ETH-USDT-SWAP",
        algo_id="algo-a",
        sz="2",
        stop_trigger_px="2850",
        confirm=True,
    )
    assert amended["algoId"] == "algo-a"
    assert client.trade_api.kwargs == {
        "instId": "ETH-USDT-SWAP",
        "algoId": "algo-a",
        "newSz": "2",
        "newSlTriggerPx": "2850",
        "newSlOrdPx": "-1",
        "newSlTriggerPxType": "last",
    }

    canceled = client.cancel_position_stop(
        inst_id="ETH-USDT-SWAP",
        algo_id="algo-a",
        confirm=True,
    )
    assert canceled["algoId"] == "algo-a"
    assert client.trade_api.kwargs == [
        {"instId": "ETH-USDT-SWAP", "algoId": "algo-a"}
    ]
