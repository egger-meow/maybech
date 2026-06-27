from fastapi.testclient import TestClient

from src.api.app import create_app
from src.daemon.service import DaemonRunner, DaemonService
from src.daemon.position_manager_service import PositionManagerService
from src.trading.audit_event_store import AuditEventStore
from src.trading.logical_position_store import (
    LogicalPositionAllocation,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.rules import PositionRule, RuleGroup
from src.trading.strategy_store import StrategyStore
from src.trading.trade_store import TradeRecord, TradeStore


class ApiMockService(DaemonService):
    name = "api_mock"
    interval = 1.0

    def setup(self):
        pass

    def tick(self):
        pass


class StrategyApiMockService(DaemonService):
    name = "strategy"
    interval = 10.0

    def __init__(self, dry_run: bool = True):
        super().__init__()
        self.dry_run = dry_run

    def setup(self):
        pass

    def tick(self):
        pass


class ApiCloseExecutor:
    def close_position(self, **kwargs):
        return {"ordId": "api-close-order"}


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


def test_api_cors_allows_configured_local_frontend_and_rejects_other_origins():
    client = TestClient(create_app(DaemonRunner()))
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    }

    allowed = client.options("/services", headers=headers)
    rejected = client.options(
        "/services",
        headers={**headers, "Origin": "https://example.com"},
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert rejected.status_code == 400
    assert "access-control-allow-origin" not in rejected.headers


def test_api_lists_persisted_audit_events(monkeypatch, tmp_path):
    store = AuditEventStore(str(tmp_path / "trades.db"))
    store.create(
        type="position.close_condition_evaluated",
        source="position_manager",
        payload={
            "position_id": "unit-a",
            "trade_id": "trade-a",
            "matched": True,
        },
    )
    store.create(
        type="position.closed",
        source="position_manager",
        payload={
            "position_id": "unit-b",
            "trade_id": "trade-b",
            "matched": True,
        },
    )
    monkeypatch.setattr("src.api.app.AuditEventStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/audit/events?position_id=unit-a")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["type"] == "position.close_condition_evaluated"
    assert body[0]["position_id"] == "unit-a"
    assert body[0]["trade_id"] == "trade-a"
    assert body[0]["payload"]["matched"] is True


def test_api_lists_persisted_strategy_decisions(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    strategy_store = StrategyStore(db_path)
    strategy_store.create(id="breakout", name="Breakout")
    audit_store = AuditEventStore(db_path)
    audit_store.create(
        id="decision-a",
        type="strategy.action_decision",
        source="strategy",
        payload={
            "strategy_id": "breakout",
            "correlation_id": "decision-a",
            "allowed": False,
            "reason": "blocked by test",
            "pair": "ETH-USDT-SWAP",
            "signal": "long",
            "execution_status": "blocked",
        },
    )
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: strategy_store)
    monkeypatch.setattr("src.api.app.AuditEventStore", lambda: audit_store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get(
        "/strategies/breakout/decisions?allowed=false&execution_status=blocked"
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": "decision-a",
            "correlation_id": "decision-a",
            "strategy_id": "breakout",
            "allowed": False,
            "reason": "blocked by test",
            "pair": "ETH-USDT-SWAP",
            "signal": "long",
            "time": None,
            "setup_reason": None,
            "entry_price": None,
            "stop_loss": None,
            "take_profit": None,
            "btc_direction": None,
            "btc_strength": None,
            "btc_impulse": None,
            "dry_run": None,
            "execution_status": "blocked",
            "execution_result": {},
            "order_id": None,
            "trade_id": None,
            "position_id": None,
            "persistence_error": None,
            "created_at": response.json()[0]["created_at"],
            "completed_at": None,
        }
    ]


def test_api_records_confirmed_partial_fills_idempotently(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    audit_store = AuditEventStore(db_path)
    trade = TradeRecord(
        id="trade-fill",
        strategy_id="strategy-a",
        inst_id="ETH-USDT-SWAP",
        side="long",
        entry_price=2000,
        status="pending_open",
        metadata_json='{"correlation_id":"decision-fill","expected_quantity":0.1}',
    )
    trade_store.save_trade(trade)
    position_store.save(LogicalPositionRecord.from_trade(trade))
    audit_store.create(
        id="decision-fill",
        type="strategy.action_decision",
        source="strategy",
        payload={
            "strategy_id": "strategy-a",
            "correlation_id": "decision-fill",
            "allowed": True,
            "execution_status": "submitted",
        },
    )
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *args: position_store)
    monkeypatch.setattr("src.api.app.AuditEventStore", lambda *args: audit_store)
    client = TestClient(create_app(DaemonRunner()))
    first_fill = {
        "fill_id": "fill-1",
        "action": "open",
        "quantity": 0.04,
        "price": 2000,
        "fee": -0.02,
        "exchange_order_id": "order-1",
        "correlation_id": "decision-fill",
        "confirmation_source": "okx_fill",
    }

    first = client.post("/positions/logical/trade-fill/allocations", json=first_fill)
    duplicate = client.post("/positions/logical/trade-fill/allocations", json=first_fill)
    second = client.post(
        "/positions/logical/trade-fill/allocations",
        json={**first_fill, "fill_id": "fill-2", "quantity": 0.06, "price": 2100},
    )
    listed = client.get("/positions/logical/trade-fill/allocations")

    assert first.status_code == 201
    assert first.json()["execution_status"] == "partially_filled"
    assert first.json()["opened_quantity"] == 0.04
    assert duplicate.status_code == 201
    assert duplicate.json()["idempotent"] is True
    assert second.status_code == 201
    assert second.json()["execution_status"] == "filled"
    assert second.json()["opened_quantity"] == 0.1
    assert second.json()["average_entry_price"] == 2060.0
    assert len(listed.json()) == 2
    assert trade_store.get_trade("trade-fill").status == "open"
    decision = audit_store.list_strategy_decisions(strategy_id="strategy-a")[0]
    assert decision.payload["execution_status"] == "filled"
    assert decision.payload["filled_quantity"] == 0.1
    assert len(audit_store.list(event_type="position.allocation_confirmed")) == 2


def test_api_rejects_conflicting_confirmed_fill_id(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="unit-a",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=2000,
        )
    )
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *args: position_store)
    monkeypatch.setattr(
        "src.api.app.AuditEventStore",
        lambda *args: AuditEventStore(db_path),
    )
    client = TestClient(create_app(DaemonRunner()))
    payload = {
        "fill_id": "fill-a",
        "action": "reduce",
        "quantity": 0.01,
        "price": 2100,
        "confirmation_source": "recovery",
    }

    assert client.post("/positions/logical/unit-a/allocations", json=payload).status_code == 201
    conflict = client.post(
        "/positions/logical/unit-a/allocations",
        json={**payload, "quantity": 0.02},
    )

    assert conflict.status_code == 409
    assert position_store.get("unit-a").remaining_quantity == 0.09


def test_manual_close_api_delegates_to_confirmed_close_lifecycle(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    trade = TradeRecord(
        id="trade-close",
        inst_id="ETH-USDT-SWAP",
        side="long",
        entry_price=2000,
    )
    store.save_trade(trade)
    LogicalPositionStore(store.db_path).save(
        LogicalPositionRecord(
            id=trade.id,
            trade_id=trade.id,
            inst_id=trade.inst_id,
            side=trade.side,
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=trade.entry_price,
        )
    )
    manager = PositionManagerService(
        store,
        dry_run=False,
        close_executor=ApiCloseExecutor(),
    )
    runner = DaemonRunner()
    runner.register(manager)
    runner.runtime.set_value(
        "account.snapshot",
        {"positions": [{"inst_id": trade.inst_id, "mark_price": "2050"}]},
    )
    client = TestClient(create_app(runner))

    missing_confirmation = client.post(
        f"/positions/logical/{trade.id}/close",
        json={"reason": "operator exit"},
    )
    response = client.post(
        f"/positions/logical/{trade.id}/close",
        json={"confirm": True, "reason": "operator exit"},
    )

    assert missing_confirmation.status_code == 422
    assert response.status_code == 200
    assert response.json()["action"] == "close_submitted"
    assert response.json()["exchange_order_id"] == "api-close-order"
    assert LogicalPositionStore(store.db_path).get(trade.id).status == "closing"
    assert store.get_trade(trade.id).status == "open"


def test_api_validates_and_evaluates_signal_expression():
    client = TestClient(create_app(DaemonRunner()))

    templates = client.get("/signals/templates")
    validation = client.post(
        "/signals/validate",
        json={"expression": {"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65000}},
    )
    evaluation = client.post(
        "/signals/evaluate",
        json={
            "expression": {"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65000},
            "context": {"prices": {"BTC-USDT-SWAP": 66000}},
        },
    )

    assert templates.status_code == 200
    assert any(item["type"] == "rapid_drop" for item in templates.json())
    assert validation.status_code == 200
    assert validation.json()["valid"] is True
    assert evaluation.status_code == 200
    assert evaluation.json()["matched"] is True


def test_api_builds_signal_runtime_context_from_runner_snapshots():
    runner = DaemonRunner()
    runner.runtime.set_value(
        "market.btc_regime",
        {"symbol": "BTC-USDT-SWAP", "price": 66000, "change_pct": -1.2},
    )
    runner.runtime.set_value(
        "account.snapshot",
        {
            "summary": {},
            "positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "3050"}],
            "orders": [],
        },
    )
    client = TestClient(create_app(runner))

    response = client.get("/signals/context")

    assert response.status_code == 200
    context = response.json()
    assert context["prices"]["BTC-USDT-SWAP"] == 66000
    assert context["prices"]["ETH-USDT-SWAP"] == 3050
    assert context["changes_pct"]["BTC-USDT-SWAP:60"] == -1.2


def test_api_evaluates_signal_with_runtime_context():
    runner = DaemonRunner()
    runner.runtime.set_value(
        "market.btc_regime",
        {"symbol": "BTC-USDT-SWAP", "price": 66000, "change_pct": 1.5},
    )
    client = TestClient(create_app(runner))

    response = client.post(
        "/signals/evaluate",
        json={
            "expression": {"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65000},
            "use_runtime_context": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["matched"] is True


def test_api_evaluation_payload_context_overrides_runtime_context():
    runner = DaemonRunner()
    runner.runtime.set_value(
        "market.btc_regime",
        {"symbol": "BTC-USDT-SWAP", "price": 64000, "change_pct": 0.1},
    )
    client = TestClient(create_app(runner))

    response = client.post(
        "/signals/evaluate",
        json={
            "expression": {"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65000},
            "context": {"prices": {"BTC-USDT-SWAP": 66000}},
            "use_runtime_context": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["matched"] is True


def test_api_evaluates_signal_with_candle_context(monkeypatch):
    import pandas as pd

    class FakeOKXClient:
        pass

    class FakeCandleManager:
        def __init__(self, client):
            self.client = client

        def fetch(self, inst_id: str, bar: str = "1m", limit: int = 100):
            assert inst_id == "BTC-USDT-SWAP"
            assert bar == "1m"
            assert limit == 20
            return pd.DataFrame(
                [
                    {"timestamp": "2026-01-01T00:00:00Z", "close": 100, "volume": 10},
                    {"timestamp": "2026-01-01T00:01:00Z", "close": 99, "volume": 10},
                    {"timestamp": "2026-01-01T00:02:00Z", "close": 98, "volume": 10},
                    {"timestamp": "2026-01-01T00:03:00Z", "close": 97, "volume": 10},
                    {"timestamp": "2026-01-01T00:04:00Z", "close": 96, "volume": 10},
                    {"timestamp": "2026-01-01T00:05:00Z", "close": 94, "volume": 30},
                ]
            )

    monkeypatch.setattr("src.api.app.OKXClient", FakeOKXClient)
    monkeypatch.setattr("src.api.app.CandleManager", FakeCandleManager)
    client = TestClient(create_app(DaemonRunner()))

    response = client.post(
        "/signals/evaluate",
        json={
            "expression": {
                "type": "rapid_drop",
                "symbol": "BTC-USDT-SWAP",
                "window_seconds": 300,
                "change_pct": 5,
            },
            "use_candle_context": True,
            "bar": "1m",
            "candle_limit": 20,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["matched"] is True
    assert body["evidence"]["change_pct"] == -6.0


def test_api_can_include_candles_in_signal_context(monkeypatch):
    import pandas as pd

    class FakeOKXClient:
        pass

    class FakeCandleManager:
        def __init__(self, client):
            self.client = client

        def fetch(self, inst_id: str, bar: str = "1m", limit: int = 100):
            return pd.DataFrame(
                [
                    {"timestamp": "2026-01-01T00:00:00Z", "close": 100, "volume": 10},
                    {"timestamp": "2026-01-01T00:01:00Z", "close": 101, "volume": 20},
                ]
            )

    monkeypatch.setattr("src.api.app.OKXClient", FakeOKXClient)
    monkeypatch.setattr("src.api.app.CandleManager", FakeCandleManager)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get(
        "/signals/context?include_candles=true&symbols=BTC-USDT-SWAP&bar=1m&candle_limit=20"
    )

    assert response.status_code == 200
    context = response.json()
    assert context["prices"]["BTC-USDT-SWAP"] == 101.0
    assert context["source"]["candles"]["requested_symbols"] == ["BTC-USDT-SWAP"]


def test_api_reports_signal_validation_errors():
    client = TestClient(create_app(DaemonRunner()))

    response = client.post(
        "/signals/validate",
        json={"expression": {"type": "price_above", "value": "bad"}},
    )

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert any("symbol" in error for error in response.json()["errors"])


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


def test_openapi_exposes_frontend_contract_schemas():
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/openapi.json")

    assert response.status_code == 200
    spec = response.json()
    schemas = spec["components"]["schemas"]
    assert "StrategySummaryResponse" in schemas
    assert "StrategyCreate" in schemas
    assert "SignalExpressionResponse" in schemas
    assert "LogicalPositionUnitResponse" in schemas
    assert "LogicalPositionCloseConditionResponse" in schemas
    assert "ConfirmedPositionFillCreate" in schemas
    assert "ConfirmedPositionFillResponse" in schemas
    assert "LogicalPositionCloseRequest" in schemas
    assert "LogicalPositionCloseResponse" in schemas
    assert "ExecutionFillIngestionStatusResponse" in schemas
    assert "AuditEventResponse" in schemas
    assert "ServiceStatusResponse" in schemas
    assert "/strategies" in spec["paths"]
    assert "/strategies/{strategy_id}/signals" in spec["paths"]
    assert "/strategies/{strategy_id}/decisions" in spec["paths"]
    assert "/signals/templates" in spec["paths"]
    assert "/signals/context" in spec["paths"]
    assert "/signals/validate" in spec["paths"]
    assert "/signals/evaluate" in spec["paths"]
    assert "/audit/events" in spec["paths"]
    assert "/execution/fills/status" in spec["paths"]
    assert "/positions/logical" in spec["paths"]
    assert "/positions/logical/{position_id}/close-conditions" in spec["paths"]
    assert "/positions/logical/{position_id}/allocations" in spec["paths"]
    assert "/positions/logical/{position_id}/close" in spec["paths"]
    assert "/positions/logical/{position_id}/close-conditions/{condition_id}" in spec["paths"]


def test_api_returns_strategy_summaries_with_runtime_state(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(
        id="breakout",
        name="Breakout",
        enabled=True,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "price_above", "symbol": "self", "value": 100},
        default_rules={"close_conditions": [{
            "purpose": "stop_loss",
            "expression": {"type": "price_below", "symbol": "self", "value": 90},
        }]},
        metadata={
            "position_side": "long",
            "order_size_contracts": {"ETH-USDT-SWAP": "1"},
        },
    )
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    runner = DaemonRunner()
    runner.register(StrategyApiMockService(dry_run=True))
    runner.runtime.set_value(
        "strategy.decisions",
        [
            {
                "allowed": True,
                "reason": "BTC regime permits short action",
                "pair": "ETH-USDT-SWAP",
                "signal": "short",
            }
        ],
    )
    client = TestClient(create_app(runner))

    response = client.get("/strategies")

    assert response.status_code == 200
    strategy = response.json()[0]
    assert strategy["id"] == "breakout"
    assert strategy["enabled"] is True
    assert strategy["runtime"]["dry_run"] is True
    assert strategy["runtime"]["service"]["name"] == "strategy"
    assert strategy["runtime"]["latest_decisions"][0]["pair"] == "ETH-USDT-SWAP"


def test_api_returns_404_for_unknown_strategy():
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/strategies/missing")

    assert response.status_code == 404


def test_api_returns_execution_fill_ingestion_status():
    runner = DaemonRunner()
    runner.runtime.set_value(
        "execution.fills.status",
        {
            "fetched": 5,
            "applied": 2,
            "idempotent": 1,
            "unmatched": 1,
            "invalid": 1,
            "conflicts": 0,
            "pages_fetched": 3,
            "caught_up": True,
            "high_water_bill_id": "bill-300",
            "updated_at": "2026-06-27T00:00:00+00:00",
        },
    )
    client = TestClient(create_app(runner))

    response = client.get("/execution/fills/status")

    assert response.status_code == 200
    assert response.json()["applied"] == 2
    assert response.json()["unmatched"] == 1
    assert response.json()["caught_up"] is True
    assert response.json()["pages_fetched"] == 3
    assert response.json()["high_water_bill_id"] == "bill-300"


def test_api_creates_and_updates_persisted_strategy(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    created = client.post(
        "/strategies",
        json={
            "id": "breakout",
            "name": "Breakout",
            "kind": "signal",
            "enabled": False,
            "target_instruments": ["ETH-USDT-SWAP"],
            "entry_signal": {"type": "price_above", "symbol": "self", "value": 3000},
            "default_rules": {"close_conditions": [{
                "purpose": "stop_loss",
                "expression": {"type": "price_below", "symbol": "self", "value": 2800},
            }]},
            "metadata": {
                "owner": "operator",
                "position_side": "long",
                "order_size_contracts": {"ETH-USDT-SWAP": "1"},
            },
        },
    )
    updated = client.patch(
        "/strategies/breakout",
        json={"target_instruments": ["ETH-USDT-SWAP", "SOL-USDT-SWAP"]},
    )

    assert created.status_code == 201
    assert created.json()["id"] == "breakout"
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["target_instruments"] == ["ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    assert store.get("breakout").enabled is False


def test_api_enables_and_disables_persisted_strategy(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(
        id="breakout",
        name="Breakout",
        enabled=False,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65000},
        default_rules={"close_conditions": [{
            "purpose": "stop_loss",
            "expression": {"type": "price_below", "symbol": "self", "value": 90},
        }]},
        metadata={
            "position_side": "long",
            "order_size_contracts": {"ETH-USDT-SWAP": "1"},
        },
    )
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    enabled = client.post("/strategies/breakout/enable")
    disabled = client.post("/strategies/breakout/disable")

    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


def test_api_rejects_enable_when_strategy_signal_is_invalid(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(
        id="breakout",
        name="Breakout",
        enabled=False,
        entry_signal={"type": "price_above", "value": "bad"},
    )
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.post("/strategies/breakout/enable")

    assert response.status_code == 400
    assert "Strategy is not executable" in response.json()["detail"]["message"]


def test_api_disables_enabled_strategy_when_edit_makes_it_invalid(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(
        id="breakout",
        name="Breakout",
        enabled=True,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "price_above", "symbol": "self", "value": 100},
        default_rules={"close_conditions": [{
            "purpose": "stop_loss",
            "expression": {"type": "price_below", "symbol": "self", "value": 90},
        }]},
        metadata={
            "position_side": "long",
            "order_size_contracts": {"ETH-USDT-SWAP": "1"},
        },
    )
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.patch("/strategies/breakout", json={"default_rules": {}})

    assert response.status_code == 400
    assert store.get("breakout").enabled is False


def test_api_creates_and_lists_strategy_signal_expressions(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(id="breakout", name="Breakout", enabled=True)
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    created = client.post(
        "/strategies/breakout/signals",
        json={
            "purpose": "entry",
            "expression": {
                "op": "and",
                "conditions": [
                    {"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65000}
                ],
            },
        },
    )
    listed = client.get("/strategies/breakout/signals")

    assert created.status_code == 201
    assert created.json()["strategy_id"] == "breakout"
    assert created.json()["expression"]["op"] == "and"
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == created.json()["id"]


def test_api_rejects_invalid_strategy_signal_expression(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(id="breakout", name="Breakout", enabled=True)
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.post(
        "/strategies/breakout/signals",
        json={"purpose": "entry", "expression": {"type": "unknown_signal"}},
    )

    assert response.status_code == 400
    assert "Signal expression validation failed" in response.json()["detail"]["message"]


def test_api_projects_open_trades_as_logical_positions(monkeypatch, tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    trade = TradeRecord(
        id="lp-1",
        strategy_id="strategy-a",
        inst_id="ETH-USDT-SWAP",
        side="long",
        entry_price=3000.0,
    )
    store.save_trade(trade)
    rule = RuleGroup(
        id="stop-loss",
        name="Stop loss",
        rules=[PositionRule(target="self", metric="price", operator="less_than", value=2900)],
    )
    store.attach_rule_group(trade.id, rule)
    position_store = LogicalPositionStore(store.db_path)
    position_store.ensure_from_trade(trade)
    position_store.create_close_condition(
        id="lp-stop-loss",
        position_id=trade.id,
        purpose="stop_loss",
        expression={"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 2900},
    )

    runner = DaemonRunner()
    runner.runtime.set_value(
        "account.snapshot",
        {
            "summary": {},
            "positions": [
                {
                    "inst_id": "ETH-USDT-SWAP",
                    "pos_side": "long",
                    "position": "0.1",
                    "avg_price": "3000",
                }
            ],
            "orders": [],
        },
    )
    runner.runtime.set_value(
        "position.intents",
        [
            {
                "inst_id": "ETH-USDT-SWAP",
                "side": "long",
                "action": "hold",
                "reason": "BTC regime supports the position",
            }
        ],
    )
    runner.runtime.events.publish("position.rule_attached", "test", {"trade_id": trade.id})

    monkeypatch.setattr("src.api.app.TradeStore", lambda: store)
    client = TestClient(create_app(runner))

    response = client.get("/positions/logical")

    assert response.status_code == 200
    logical = response.json()[0]
    assert logical["id"] == trade.id
    assert logical["source"] == "strategy"
    assert logical["strategy_id"] == "strategy-a"
    assert logical["trade_id"] == trade.id
    assert logical["metadata"]["backfilled_from_trade"] is True
    assert logical["close_conditions"][0]["id"] == "lp-stop-loss"
    assert logical["close_conditions"][0]["purpose"] == "stop_loss"
    assert logical["legacy_trade_rules"][0]["group"]["id"] == "stop-loss"
    assert logical["current_intent"]["action"] == "hold"
    assert logical["okx_net_position"]["position"] == "0.1"
    assert logical["audit_events"][0]["type"] == "position.rule_attached"


def test_api_returns_persisted_logical_position_without_trade(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="manual-1",
            source="manual",
            inst_id="BTC-USDT-SWAP",
            side="short",
            opened_quantity=0.01,
            remaining_quantity=0.01,
            entry_price=65000,
        )
    )
    position_store.record_allocation(
        LogicalPositionAllocation(
            id="alloc-1",
            position_id="manual-1",
            action="open",
            quantity=0.01,
            price=65000,
            reason="manual import",
        ),
        apply_to_position=False,
    )

    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/positions/logical")

    assert response.status_code == 200
    logical = response.json()[0]
    assert logical["id"] == "manual-1"
    assert logical["source"] == "manual"
    assert logical["trade_id"] is None
    assert logical["created_at"]
    assert logical["opened_quantity"] == 0.01
    assert logical["allocations"][0]["id"] == "alloc-1"
    assert logical["close_conditions"] == []
    assert logical["legacy_trade_rules"] == []


def test_api_includes_logical_position_reconciliation(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="unit-a",
            source="manual",
            inst_id="ETH-USDT-SWAP",
            side="short",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=3000,
        )
    )
    position_store.save(
        LogicalPositionRecord(
            id="unit-b",
            source="manual",
            inst_id="ETH-USDT-SWAP",
            side="short",
            opened_quantity=0.2,
            remaining_quantity=0.2,
            entry_price=3010,
        )
    )
    runner = DaemonRunner()
    runner.runtime.set_value(
        "account.snapshot",
        {
            "summary": {},
            "positions": [{"inst_id": "ETH-USDT-SWAP", "pos_side": "short", "position": "0.3"}],
            "orders": [],
        },
    )

    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    client = TestClient(create_app(runner))

    response = client.get("/positions/logical")

    assert response.status_code == 200
    by_id = {item["id"]: item for item in response.json()}
    assert by_id["unit-a"]["reconciliation"]["state"] == "balanced"
    assert by_id["unit-b"]["reconciliation"]["exchange_position_key"] == "ETH-USDT-SWAP:short"
    assert by_id["unit-a"]["metadata"]["reconciliation"]["state"] == "balanced"


def test_api_returns_one_logical_position_by_id(monkeypatch, tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    trade = TradeRecord(id="lp-2", inst_id="SOL-USDT-SWAP", side="short", entry_price=120.0)
    store.save_trade(trade)

    monkeypatch.setattr("src.api.app.TradeStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get(f"/positions/logical/{trade.id}")

    assert response.status_code == 200
    assert response.json()["id"] == trade.id
    assert response.json()["source"] == "manual"


def test_api_returns_404_for_missing_logical_position(monkeypatch, tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    monkeypatch.setattr("src.api.app.TradeStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/positions/logical/missing")

    assert response.status_code == 404


def test_api_manages_logical_position_close_conditions(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="unit-a",
            source="manual",
            inst_id="ETH-USDT-SWAP",
            side="long",
            entry_price=3000,
        )
    )

    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    client = TestClient(create_app(DaemonRunner()))

    created = client.post(
        "/positions/logical/unit-a/close-conditions",
        json={
            "purpose": "stop_loss",
            "expression": {"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 2900},
            "metadata": {"label": "hard stop"},
        },
    )

    assert created.status_code == 201
    condition_id = created.json()["id"]
    assert created.json()["position_id"] == "unit-a"
    assert created.json()["expression"]["value"] == 2900

    listed = client.get("/positions/logical/unit-a/close-conditions")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == condition_id

    updated = client.patch(
        f"/positions/logical/unit-a/close-conditions/{condition_id}",
        json={
            "enabled": False,
            "expression": {"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 2910},
        },
    )

    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["expression"]["value"] == 2910
    assert client.get("/positions/logical/unit-a/close-conditions?enabled=true").json() == []

    deleted = client.delete(f"/positions/logical/unit-a/close-conditions/{condition_id}")
    assert deleted.status_code == 200
    assert client.get("/positions/logical/unit-a/close-conditions").json() == []


def test_api_rejects_invalid_logical_position_close_condition(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(LogicalPositionRecord(id="unit-a", inst_id="ETH-USDT-SWAP", side="long", entry_price=3000))

    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.post(
        "/positions/logical/unit-a/close-conditions",
        json={"purpose": "stop_loss", "expression": {"type": "price_below", "value": "bad"}},
    )

    assert response.status_code == 400
    assert "Signal expression validation failed" in response.json()["detail"]["message"]
