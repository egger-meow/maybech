from fastapi.testclient import TestClient

from src.api.app import create_app
from src.daemon.service import DaemonRunner, DaemonService
from src.trading.rules import PositionRule, RuleGroup
from src.trading.trade_store import TradeRecord, TradeStore


class ApiMockService(DaemonService):
    name = "api_mock"
    interval = 1.0

    def setup(self):
        pass

    def tick(self):
        pass


def test_api_lists_services_and_events():
    runner = DaemonRunner()
    runner.register(ApiMockService())
    runner.runtime.events.publish("test.event", "test", {"value": 1})

    client = TestClient(create_app(runner))

    services = client.get("/services")
    events = client.get("/events")

    assert services.status_code == 200
    assert services.json()["api_mock"]["active"] is True
    assert events.status_code == 200
    assert events.json()[-1]["type"] == "test.event"


def test_api_can_disable_and_enable_service():
    runner = DaemonRunner()
    runner.register(ApiMockService())
    client = TestClient(create_app(runner))

    disabled = client.post("/services/api_mock/disable")
    enabled = client.post("/services/api_mock/enable")

    assert disabled.status_code == 200
    assert disabled.json()["active"] is False
    assert enabled.status_code == 200
    assert enabled.json()["active"] is True


def test_api_returns_404_for_unknown_service():
    runner = DaemonRunner()
    client = TestClient(create_app(runner))

    response = client.get("/services/missing")

    assert response.status_code == 404


def test_api_returns_latest_btc_regime():
    runner = DaemonRunner()
    runner.runtime.set_value(
        "market.btc_regime",
        {"symbol": "BTC-USDT-SWAP", "direction": "bullish"},
    )
    client = TestClient(create_app(runner))

    response = client.get("/market/btc-regime")

    assert response.status_code == 200
    assert response.json()["direction"] == "bullish"


def test_api_returns_404_when_btc_regime_missing():
    runner = DaemonRunner()
    client = TestClient(create_app(runner))

    response = client.get("/market/btc-regime")

    assert response.status_code == 404


def test_api_returns_strategy_decisions_snapshot():
    runner = DaemonRunner()
    runner.runtime.set_value(
        "strategy.decisions",
        [{"pair": "ETH-USDT-SWAP", "allowed": False, "reason": "blocked"}],
    )
    client = TestClient(create_app(runner))

    response = client.get("/strategy/decisions")

    assert response.status_code == 200
    assert response.json()[0]["allowed"] is False


def test_api_returns_position_intents_snapshot():
    runner = DaemonRunner()
    runner.runtime.set_value(
        "position.intents",
        [{"inst_id": "ETH-USDT-SWAP", "action": "reduce", "reason": "btc against"}],
    )
    client = TestClient(create_app(runner))

    response = client.get("/position/intents")

    assert response.status_code == 200
    assert response.json()[0]["action"] == "reduce"


def test_api_returns_empty_strategy_decisions_when_missing():
    runner = DaemonRunner()
    client = TestClient(create_app(runner))

    response = client.get("/strategy/decisions")

    assert response.status_code == 200
    assert response.json() == []


def test_api_returns_empty_position_intents_when_missing():
    runner = DaemonRunner()
    client = TestClient(create_app(runner))

    response = client.get("/position/intents")

    assert response.status_code == 200
    assert response.json() == []


def test_api_returns_account_snapshot_positions_and_orders():
    runner = DaemonRunner()
    runner.runtime.set_value(
        "account.snapshot",
        {
            "summary": {"total_equity": "1000"},
            "positions": [{"inst_id": "BTC-USDT-SWAP", "position": "1"}],
            "orders": [{"inst_id": "BTC-USDT-SWAP", "state": "filled"}],
        },
    )
    client = TestClient(create_app(runner))

    snapshot = client.get("/account/snapshot")
    positions = client.get("/account/positions")
    orders = client.get("/account/orders")

    assert snapshot.status_code == 200
    assert snapshot.json()["summary"]["total_equity"] == "1000"
    assert positions.status_code == 200
    assert positions.json()[0]["position"] == "1"
    assert orders.status_code == 200
    assert orders.json()[0]["state"] == "filled"


def test_api_returns_empty_account_snapshot_when_missing():
    runner = DaemonRunner()
    client = TestClient(create_app(runner))

    snapshot = client.get("/account/snapshot")
    positions = client.get("/account/positions")
    orders = client.get("/account/orders")

    assert snapshot.status_code == 200
    assert snapshot.json() == {"summary": {}, "positions": [], "orders": []}
    assert positions.json() == []
    assert orders.json() == []


def test_api_delete_rule_is_scoped_to_trade(monkeypatch, tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    first_trade = TradeRecord(id="trade-a", inst_id="ETH-USDT-SWAP", side="long", entry_price=100)
    second_trade = TradeRecord(id="trade-b", inst_id="SOL-USDT-SWAP", side="long", entry_price=50)
    store.save_trade(first_trade)
    store.save_trade(second_trade)

    rule = RuleGroup(
        id="rule-on-b",
        name="SOL stop",
        rules=[PositionRule(target="self", metric="price", operator="less_than", value=45)],
    )
    store.attach_rule_group(second_trade.id, rule)

    monkeypatch.setattr("src.api.app.TradeStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.delete(f"/trades/{first_trade.id}/rules/{rule.id}")

    assert response.status_code == 404
    assert store.get_trade_rules(second_trade.id)[0][0].id == rule.id


def test_api_delete_rule_removes_only_matching_trade_rule(monkeypatch, tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    trade = TradeRecord(id="trade-a", inst_id="ETH-USDT-SWAP", side="long", entry_price=100)
    store.save_trade(trade)

    rule = RuleGroup(
        id="rule-on-a",
        name="ETH stop",
        rules=[PositionRule(target="self", metric="price", operator="less_than", value=90)],
    )
    store.attach_rule_group(trade.id, rule)

    monkeypatch.setattr("src.api.app.TradeStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.delete(f"/trades/{trade.id}/rules/{rule.id}")

    assert response.status_code == 200
    assert store.get_trade_rules(trade.id) == []
