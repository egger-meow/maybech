import pytest

import src.exchange.client as client_module
from src.exchange.client import OKXClient


class FakeTradeApi:
    def __init__(self):
        self.kwargs = None

    def get_fills_history(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"billId": "bill-a"}]}

    def place_order(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"ordId": "close-order"}]}

    def get_order(self, **kwargs):
        self.kwargs = kwargs
        return {
            "code": "0",
            "data": [{"ordId": kwargs.get("ordId", "recovered-order"), "state": "live"}],
        }

    def get_order_list(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"ordId": "pending-a"}]}


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

    assert result == {"ordId": "close-order"}
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
