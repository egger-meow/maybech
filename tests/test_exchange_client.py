import src.exchange.client as client_module
from src.exchange.client import OKXClient


class FakeTradeApi:
    def __init__(self):
        self.kwargs = None

    def get_fills(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"tradeId": "fill-a"}]}

    def place_order(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"ordId": "close-order"}]}

    def get_order(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"ordId": kwargs["ordId"], "state": "live"}]}


class FakePublicApi:
    def __init__(self):
        self.kwargs = None

    def get_instruments(self, **kwargs):
        self.kwargs = kwargs
        return {"code": "0", "data": [{"instId": kwargs["instId"]}]}


def test_okx_client_get_fills_uses_authenticated_swap_endpoint():
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()

    fills = client.get_fills(inst_type="SWAP", limit="50", after="cursor-a")

    assert fills == [{"tradeId": "fill-a"}]
    assert client.trade_api.kwargs == {
        "instType": "SWAP",
        "limit": "50",
        "after": "cursor-a",
    }


def test_okx_client_places_guarded_reduce_only_close(monkeypatch):
    client = object.__new__(OKXClient)
    client.trade_api = FakeTradeApi()
    monkeypatch.setattr(client_module, "_ORDER_PLACEMENT_ARMED", True)

    result = client.place_reduce_market_order(
        inst_id="ETH-USDT-SWAP",
        position_side="long",
        sz="0.1",
        confirm=True,
    )

    assert result == {"ordId": "close-order"}
    assert client.trade_api.kwargs == {
        "instId": "ETH-USDT-SWAP",
        "tdMode": "cross",
        "side": "sell",
        "ordType": "market",
        "sz": "0.1",
        "posSide": "",
        "reduceOnly": "true",
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


def test_okx_client_get_instruments_uses_public_endpoint():
    client = object.__new__(OKXClient)
    client.public_api = FakePublicApi()

    instruments = client.get_instruments(inst_type="SWAP", inst_id="ETH-USDT-SWAP")

    assert instruments == [{"instId": "ETH-USDT-SWAP"}]
    assert client.public_api.kwargs == {
        "instType": "SWAP",
        "instId": "ETH-USDT-SWAP",
    }
