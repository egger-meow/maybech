from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.api.app import create_app
from src.daemon.service import DaemonRunner, DaemonService
from src.daemon.position_manager_service import PositionManagerService
from src.exchange.client import arm_order_placement, disarm_order_placement
from src.trading.account_risk import AccountRiskLimits, AccountRiskStore
from src.trading.entry_control import EntryControlManager
from src.trading.audit_event_store import AuditEventStore
from src.trading.logical_position_store import (
    LogicalPositionAllocation,
    LogicalPositionProtection,
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


class NotificationApiMockService(DaemonService):
    name = "lifecycle_notifications"
    interval = 2.0

    def setup(self):
        pass

    def tick(self):
        pass


class SuccessfulNotificationTestNotifier:
    enabled = True
    last_error = ""

    def send(self, *parts: str) -> bool:
        return True


def test_notification_health_exposes_readiness_without_credentials(tmp_path, monkeypatch):
    store = AuditEventStore(str(tmp_path / "notifications.db"))
    store.record_notification_delivery_attempt(
        "lifecycle_notifications",
        "line",
        event_id="line-health",
        succeeded=False,
        error="TimeoutError",
    )
    monkeypatch.setattr("src.api.app.AuditEventStore", lambda *args: store)
    from src.api.app import settings as api_settings
    monkeypatch.setattr(
        "src.api.app.settings",
        replace(
            api_settings,
            LINE_CHANNEL_ACCESS_TOKEN="secret-token",
            LINE_CHANNEL_SECRET="secret-value",
            LINE_USER_ID="private-user",
        ),
    )
    runner = DaemonRunner()
    runner.register(NotificationApiMockService())

    body = TestClient(create_app(runner)).get("/notifications/health").json()

    assert body["service_enabled"] is True
    assert body["channels"][0]["channel"] == "line"
    assert body["channels"][0]["state"] == "backoff"
    assert body["channels"][0]["last_error"] == "TimeoutError"
    serialized = str(body)
    assert "secret-token" not in serialized
    assert "secret-value" not in serialized
    assert "private-user" not in serialized


def test_notification_test_requires_confirmation_and_persists_result(tmp_path, monkeypatch):
    store = AuditEventStore(str(tmp_path / "notifications.db"))
    monkeypatch.setattr("src.api.app.AuditEventStore", lambda *args: store)
    monkeypatch.setattr("src.api.app.LineBotNotifier", SuccessfulNotificationTestNotifier)
    client = TestClient(create_app(DaemonRunner()))

    rejected = client.post("/notifications/test", json={"channel": "line"})
    response = client.post(
        "/notifications/test",
        json={"confirm": True, "channel": "line"},
    )

    assert rejected.status_code == 422
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert store.notification_delivery_health("lifecycle_notifications")["line"][
        "last_success_at"
    ]
    assert len(store.list(event_type="notification.test_completed")) == 1


def test_runtime_capabilities_distinguish_leader_and_read_replica():
    leader = TestClient(create_app(DaemonRunner()))
    replica = TestClient(create_app(DaemonRunner(), runtime_role="replica"))

    leader_body = leader.get("/runtime/capabilities").json()
    replica_body = replica.get("/runtime/capabilities").json()

    assert leader_body["execution_leader"] is True
    assert leader_body["product_mutations_available"] is True
    assert replica_body["execution_leader"] is False
    assert replica_body["horizontal_read_replica"] is True
    assert replica_body["product_mutations_available"] is False


def test_read_replica_rejects_mutations_before_route_execution():
    client = TestClient(create_app(DaemonRunner(), runtime_role="replica"))

    response = client.post("/strategies", json={"name": "must-not-write"})

    assert response.status_code == 503
    assert "read-only" in response.json()["detail"]


def test_read_replica_rejects_leader_only_runtime_reads():
    client = TestClient(create_app(DaemonRunner(), runtime_role="replica"))

    response = client.get("/account/snapshot")

    assert response.status_code == 503
    assert "execution leader" in response.json()["detail"]


def test_read_replica_rejects_live_event_websocket():
    client = TestClient(create_app(DaemonRunner(), runtime_role="replica"))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/events"):
            pass

    assert exc_info.value.code == 1013


def test_read_replica_position_get_does_not_backfill_or_reconcile(monkeypatch, tmp_path):
    db_path = str(tmp_path / "replica.db")
    trade_store = TradeStore(db_path)
    trade_store.save_trade(
        TradeRecord(
            id="legacy-only",
            strategy_id="legacy",
            inst_id="BTC-USDT-SWAP",
            side="long",
            entry_price=100,
            status="open",
        )
    )
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="persisted",
            source="manual",
            inst_id="ETH-USDT-SWAP",
            side="short",
            opened_quantity=1,
            remaining_quantity=1,
            entry_price=200,
            status="open",
        )
    )
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    client = TestClient(create_app(DaemonRunner(), runtime_role="replica"))

    response = client.get("/positions/logical?status=all")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["persisted"]
    assert response.json()[0]["reconciliation"] is None
    assert position_store.get("legacy-only") is None
    assert position_store.get("persisted").exchange_position_key == ""


def test_configured_api_token_protects_http_routes():
    client = TestClient(create_app(DaemonRunner(), api_token="secret-token"))

    missing = client.get("/services")
    wrong = client.get("/services", headers={"Authorization": "Bearer wrong"})
    allowed = client.get(
        "/services",
        headers={"Authorization": "Bearer secret-token"},
    )
    capabilities = client.get("/runtime/capabilities")

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert allowed.status_code == 200
    assert capabilities.status_code == 200
    assert capabilities.json()["authentication_required"] is True


def test_configured_api_token_protects_live_event_websocket():
    client = TestClient(create_app(DaemonRunner(), api_token="secret-token"))

    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect("/ws/events"):
            pass

    assert exc_info.value.code == 1008


def test_api_configures_and_reads_account_risk_limits(monkeypatch, tmp_path):
    store = AccountRiskStore(str(tmp_path / "trades.db"))
    monkeypatch.setattr("src.api.app.AccountRiskStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    assert client.get("/risk/limits").status_code == 404

    empty_allowlist = client.put(
        "/risk/limits",
        json={
            "confirm": True,
            "enabled": True,
            "max_order_notional_usd": 100,
            "max_total_exposure_usd": 500,
            "max_leverage": 5,
            "allowed_instruments": [],
        },
    )
    assert empty_allowlist.status_code == 422

    rejected = client.put(
        "/risk/limits",
        json={
            "confirm": False,
            "enabled": True,
            "max_order_notional_usd": 100,
            "max_total_exposure_usd": 500,
            "max_leverage": 5,
            "allowed_instruments": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        },
    )
    response = client.put(
        "/risk/limits",
        json={
            "confirm": True,
            "enabled": True,
            "max_order_notional_usd": 100,
            "max_total_exposure_usd": 500,
            "max_leverage": 5,
            "allowed_instruments": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        },
    )

    assert rejected.status_code == 422
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["max_total_exposure_usd"] == 500
    assert client.get("/risk/limits").json() == response.json()
    audits = AuditEventStore(store.db_path).list(event_type="risk.limits_updated")
    assert len(audits) == 1
    assert audits[0].payload["before"] is None
    assert audits[0].payload["after"]["max_total_exposure_usd"] == 500

    stale = client.put(
        "/risk/limits",
        json={
            "confirm": True,
            "expected_updated_at": "stale-version",
            "enabled": True,
            "max_order_notional_usd": 200,
            "max_total_exposure_usd": 1000,
            "max_leverage": 10,
            "allowed_instruments": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        },
    )
    assert stale.status_code == 409
    assert client.get("/risk/limits").json() == response.json()

    current_version = response.json()["updated_at"]
    updated = client.put(
        "/risk/limits",
        json={
            "confirm": True,
            "expected_updated_at": current_version,
            "enabled": True,
            "max_order_notional_usd": 150,
            "max_total_exposure_usd": 750,
            "max_leverage": 6,
            "allowed_instruments": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["max_order_notional_usd"] == 150


def test_api_requires_confirmation_for_entry_enable_and_kill(monkeypatch, tmp_path):
    store = AccountRiskStore(str(tmp_path / "trades.db"))
    manager = EntryControlManager(risk_store=store)
    monkeypatch.setattr("src.api.app.AccountRiskStore", lambda: store)
    monkeypatch.setattr("src.api.app.EntryControlManager", lambda: manager)
    client = TestClient(create_app(DaemonRunner()))
    client.put(
        "/risk/limits",
        json={
            "confirm": True,
            "enabled": True,
            "max_order_notional_usd": 100,
            "max_total_exposure_usd": 500,
            "max_leverage": 5,
            "allowed_instruments": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        },
    )

    assert client.post("/risk/entries/enable", json={"confirm": False}).status_code == 422
    assert client.post("/risk/entries/enable", json={"confirm": True}).status_code == 409
    arm_order_placement(preflight_passed=True)
    try:
        enabled = client.post("/risk/entries/enable", json={"confirm": True})
        killed = client.post("/risk/entries/kill", json={"confirm": True})

        assert enabled.status_code == 200
        assert enabled.json()["entries_enabled"] is True
        assert killed.status_code == 200
        assert killed.json()["entries_enabled"] is False
        assert client.get("/risk/entries").json()["entries_enabled"] is False
    finally:
        disarm_order_placement()


def test_risk_limit_update_rolls_back_when_audit_write_fails(monkeypatch, tmp_path):
    store = AccountRiskStore(str(tmp_path / "trades.db"))

    class BrokenAuditStore:
        def create(self, **kwargs):
            raise RuntimeError("audit unavailable")

    monkeypatch.setattr("src.api.app.AccountRiskStore", lambda: store)
    monkeypatch.setattr("src.api.app.AuditEventStore", lambda *args: BrokenAuditStore())
    client = TestClient(create_app(DaemonRunner()), raise_server_exceptions=False)

    response = client.put(
        "/risk/limits",
        json={
            "confirm": True,
            "enabled": True,
            "max_order_notional_usd": 100,
            "max_total_exposure_usd": 500,
            "max_leverage": 5,
            "allowed_instruments": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        },
    )

    assert response.status_code == 500
    assert store.get() is None


def test_risk_limit_update_requires_entries_disabled(monkeypatch, tmp_path):
    store = AccountRiskStore(str(tmp_path / "trades.db"))
    store.save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=100,
            max_total_exposure_usd=500,
            max_leverage=5,
            allowed_instruments=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        )
    )
    store.set_entries_enabled(True)
    monkeypatch.setattr("src.api.app.AccountRiskStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.put(
        "/risk/limits",
        json={
            "confirm": True,
            "enabled": True,
            "max_order_notional_usd": 200,
            "max_total_exposure_usd": 1000,
            "max_leverage": 10,
            "allowed_instruments": ["BTC-USDT-SWAP", "ETH-USDT-SWAP"],
        },
    )

    assert response.status_code == 409
    assert store.get().max_order_notional_usd == 100


def test_risk_limit_update_cannot_orphan_enabled_strategy_target(monkeypatch, tmp_path):
    store = AccountRiskStore(str(tmp_path / "trades.db"))
    store.save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=100,
            max_total_exposure_usd=500,
            max_leverage=5,
            allowed_instruments=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        )
    )
    StrategyStore(store.db_path).create(
        id="enabled-eth",
        name="Enabled ETH",
        enabled=True,
        target_instruments=["ETH-USDT-SWAP"],
    )
    monkeypatch.setattr("src.api.app.AccountRiskStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.put(
        "/risk/limits",
        json={
            "confirm": True,
            "expected_updated_at": store.get().updated_at,
            "enabled": True,
            "max_order_notional_usd": 100,
            "max_total_exposure_usd": 500,
            "max_leverage": 5,
            "allowed_instruments": ["BTC-USDT-SWAP"],
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["strategies"][0]["strategy_id"] == "enabled-eth"
    assert store.get().allowed_instruments == (
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
    )


def test_api_imports_only_current_unexplained_position_gap(monkeypatch, tmp_path):
    trade_store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(trade_store.db_path)

    class FakeOKXClient:
        pending = []

        def get_positions(self, *, inst_type):
            return [
                {
                    "instId": "ETH-USDT-SWAP",
                    "posSide": "net",
                    "pos": "2",
                    "avgPx": "3000",
                    "markPx": "3100",
                }
            ]

        def get_instruments(self, *, inst_type, inst_id):
            return [{
                "instId": inst_id,
                "state": "live",
                "minSz": "1",
                "lotSz": "1",
                "tickSz": "0.1",
            }]

        def get_pending_algo_orders(self, *, inst_id):
            return list(self.pending)

        def place_position_stop(self, **kwargs):
            order = {
                "algoId": "protect-api-1",
                "algoClOrdId": kwargs["algo_client_order_id"],
                "instId": kwargs["inst_id"],
                "side": "sell",
                "ordType": "conditional",
                "state": "live",
                "posSide": "net",
                "reduceOnly": "true",
                "sz": kwargs["sz"],
                "slTriggerPx": kwargs["stop_trigger_px"],
                "slOrdPx": "-1",
            }
            self.pending.append(order)
            return {"algoId": order["algoId"], "sCode": "0"}

    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *args: position_store)
    monkeypatch.setattr("src.api.app.OKXClient", FakeOKXClient)
    client = TestClient(create_app(DaemonRunner()))
    payload = {
        "confirm": True,
        "inst_id": "ETH-USDT-SWAP",
        "side": "long",
        "reason": "adopt externally opened position",
        "close_conditions": [
            {
                "purpose": "stop_loss",
                "expression": {
                    "type": "price_below",
                    "symbol": "self",
                    "value": 2900,
                },
                "enabled": True,
            }
        ],
    }

    created = client.post("/positions/import", json=payload)
    repeated = client.post("/positions/import", json=payload)

    assert created.status_code == 201
    assert created.json()["source"] == "import"
    assert created.json()["opened_quantity"] == 2
    assert created.json()["metadata"]["exchange_protection_verified"] is True
    assert repeated.status_code == 409


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


def test_api_exposes_runtime_lease_without_raw_account_id():
    runner = DaemonRunner()
    runner.runtime.set_value(
        "runtime.lease",
        {
            "held": True,
            "owner_id": "owner-a",
            "pid": 123,
            "hostname": "host-a",
            "database": "C:/data/trades.db",
            "account_scope": "abcdef123456",
            "acquired_at": "2026-06-28T00:00:00+00:00",
            "lock_root": "C:/locks",
        },
    )

    response = TestClient(create_app(runner)).get("/runtime/lease")

    assert response.status_code == 200
    assert response.json()["held"] is True
    assert response.json()["account_scope"] == "abcdef123456"


def test_api_cors_allows_configured_local_frontend_and_rejects_other_origins():
    client = TestClient(create_app(DaemonRunner()))
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "Authorization",
    }

    allowed = client.options("/services", headers=headers)
    rejected = client.options(
        "/services",
        headers={**headers, "Origin": "https://example.com"},
    )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "Authorization" in allowed.headers["access-control-allow-headers"]
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


def test_manual_reduce_api_claims_exact_quantity_without_changing_position(tmp_path):
    store = TradeStore(str(tmp_path / "trades.db"))
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(
        LogicalPositionRecord(
            id="unit-reduce",
            source="manual",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=2000,
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
        {"positions": [{"inst_id": "ETH-USDT-SWAP", "mark_price": "2050"}]},
    )
    client = TestClient(create_app(runner))

    missing_confirmation = client.post(
        "/positions/logical/unit-reduce/reduce",
        json={"quantity": 0.04, "reason": "operator trim"},
    )
    full_quantity = client.post(
        "/positions/logical/unit-reduce/reduce",
        json={"confirm": True, "quantity": 0.1, "reason": "operator trim"},
    )
    response = client.post(
        "/positions/logical/unit-reduce/reduce",
        json={"confirm": True, "quantity": 0.04, "reason": "operator trim"},
    )

    assert missing_confirmation.status_code == 422
    assert full_quantity.status_code == 409
    assert response.status_code == 200
    assert response.json()["action"] == "reduce_submitted"
    assert response.json()["quantity"] == 0.04
    pending = position_store.get("unit-reduce")
    assert pending.status == "reducing"
    assert pending.remaining_quantity == 0.1
    assert position_store.get_execution_order("api-close-order")["action"] == "reduce"


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


def test_api_returns_typed_market_candles(monkeypatch):
    import pandas as pd

    class FakeCandleManager:
        def __init__(self, client):
            del client

        def fetch(self, inst_id: str, bar: str = "1m", limit: int = 100):
            assert (inst_id, bar, limit) == ("BTC-USDT-SWAP", "5m", 2)
            return pd.DataFrame(
                [
                    {
                        "timestamp": pd.Timestamp("2026-01-01T00:00:00Z"),
                        "open": 100,
                        "high": 102,
                        "low": 99,
                        "close": 101,
                        "volume": 12,
                        "confirm": 1,
                    },
                    {
                        "timestamp": pd.Timestamp("2026-01-01T00:05:00Z"),
                        "open": 101,
                        "high": 103,
                        "low": 100,
                        "close": 102,
                        "volume": 15,
                        "confirm": 0,
                    },
                ]
            )

    monkeypatch.setattr("src.api.app.CandleManager", FakeCandleManager)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/market/candles?inst_id=BTC-USDT-SWAP&bar=5m&limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body["inst_id"] == "BTC-USDT-SWAP"
    assert body["candles"][0]["open"] == 100
    assert body["candles"][1]["confirmed"] is False


def test_api_returns_logical_position_chart_overlays(monkeypatch, tmp_path):
    import pandas as pd

    db_path = str(tmp_path / "positions.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="unit-chart",
            source="strategy",
            strategy_id="breakout",
            inst_id="BTC-USDT-SWAP",
            side="long",
            opened_quantity=2,
            remaining_quantity=2,
            entry_price=100,
            status="open",
        )
    )
    position_store.create_close_condition(
        position_id="unit-chart",
        purpose="stop_loss",
        expression={"type": "price_below", "symbol": "self", "value": 95},
        metadata={
            "break_even": {
                "target_stop": "100",
                "applied_at": "2026-01-01T00:02:00+00:00",
            }
        },
    )
    position_store.create_close_condition(
        position_id="unit-chart",
        purpose="take_profit",
        expression={"type": "price_above", "symbol": "self", "value": 120},
    )
    position_store.record_allocation(
        LogicalPositionAllocation(
            id="reduce-fill",
            position_id="unit-chart",
            action="reduce",
            quantity=1,
            price=110,
        )
    )

    class FakeCandleManager:
        def __init__(self, client):
            del client

        def fetch(self, inst_id: str, bar: str = "1m", limit: int = 100):
            assert inst_id == "BTC-USDT-SWAP"
            return pd.DataFrame(
                [
                    {
                        "timestamp": pd.Timestamp("2026-01-01T00:05:00Z"),
                        "open": 108,
                        "high": 112,
                        "low": 107,
                        "close": 111,
                        "volume": 20,
                        "confirm": 1,
                    }
                ]
            )

    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.CandleManager", FakeCandleManager)
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/positions/logical/unit-chart/chart")

    assert response.status_code == 200
    body = response.json()
    assert body["position_id"] == "unit-chart"
    assert {overlay["kind"] for overlay in body["overlays"]} == {
        "entry",
        "current",
        "stop_loss",
        "take_profit",
        "break_even",
        "execution",
    }
    assert next(item for item in body["overlays"] if item["kind"] == "current")["price"] == 111


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
    assert "LogicalPositionReduceRequest" in schemas
    assert "LogicalPositionReduceResponse" in schemas
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
    assert "/positions/logical/{position_id}/reduce" in spec["paths"]
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
            "max_entry_slippage_pct": "0.005",
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
            "websocket_enabled": True,
            "websocket_connected": True,
            "websocket_events_received": 4,
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
    assert response.json()["websocket_connected"] is True
    assert response.json()["websocket_events_received"] == 4


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
            "execution_delay_seconds": 15,
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
                "max_entry_slippage_pct": "0.005",
            },
        },
    )
    updated = client.patch(
        "/strategies/breakout",
        json={"expected_updated_at": created.json()["updated_at"], "target_instruments": ["ETH-USDT-SWAP", "SOL-USDT-SWAP"], "execution_delay_seconds": 30},
    )

    assert created.status_code == 201
    assert created.json()["id"] == "breakout"
    assert updated.status_code == 200
    assert updated.json()["enabled"] is False
    assert updated.json()["target_instruments"] == ["ETH-USDT-SWAP", "SOL-USDT-SWAP"]
    assert created.json()["execution_delay_seconds"] == 15
    assert updated.json()["execution_delay_seconds"] == 30
    assert store.get("breakout").enabled is False

    stale = client.patch(
        "/strategies/breakout",
        json={"expected_updated_at": "stale-version", "name": "Overwrite"},
    )
    assert stale.status_code == 409
    assert store.get("breakout").name == "Breakout"


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
            "max_entry_slippage_pct": "0.005",
        },
    )
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    patch_bypass = client.patch("/strategies/breakout", json={"expected_updated_at": store.get("breakout").updated_at, "enabled": True})
    current_version = store.get("breakout").updated_at
    unconfirmed = client.post("/strategies/breakout/enable", json={"confirm": False, "expected_updated_at": current_version})
    stale = client.post("/strategies/breakout/enable", json={"confirm": True, "expected_updated_at": "stale"})
    enabled = client.post("/strategies/breakout/enable", json={"confirm": True, "expected_updated_at": current_version})
    disabled = client.post("/strategies/breakout/disable")

    assert patch_bypass.status_code == 422
    assert unconfirmed.status_code == 422
    assert stale.status_code == 409
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

    response = client.post("/strategies/breakout/enable", json={"confirm": True, "expected_updated_at": store.get("breakout").updated_at})

    assert response.status_code == 400
    assert "Strategy is not executable" in response.json()["detail"]["message"]


def test_api_rejects_strategy_enable_outside_account_allowlist(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(
        id="eth-breakout",
        name="ETH Breakout",
        enabled=False,
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "price_above", "symbol": "self", "value": 100},
        default_rules={"close_conditions": [{
            "purpose": "stop_loss",
            "expression": {"type": "price_below", "symbol": "self", "value": 90},
        }]},
        metadata={
            "position_side": "long",
            "order_size_contracts": {"ETH-USDT-SWAP": "1"},
            "max_entry_slippage_pct": "0.005",
        },
    )
    AccountRiskStore(store.db_path).save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=100,
            max_total_exposure_usd=500,
            max_leverage=5,
            allowed_instruments=("BTC-USDT-SWAP",),
        )
    )
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.post("/strategies/eth-breakout/enable", json={"confirm": True, "expected_updated_at": store.get("eth-breakout").updated_at})

    assert response.status_code == 400
    assert "outside account risk allowlist" in response.json()["detail"]["errors"][0]
    assert store.get("eth-breakout").enabled is False


def test_api_rolls_back_enabled_strategy_edit_when_it_makes_it_invalid(monkeypatch, tmp_path):
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
            "max_entry_slippage_pct": "0.005",
        },
    )
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.patch("/strategies/breakout", json={"expected_updated_at": store.get("breakout").updated_at, "default_rules": {}})

    assert response.status_code == 400
    assert store.get("breakout").enabled is True
    assert store.get("breakout").default_rules["close_conditions"]


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


def test_api_edits_and_deletes_strategy_signal_with_audit(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(id="breakout", name="Breakout")
    expression = store.create_signal_expression(
        strategy_id="breakout",
        purpose="entry",
        expression={"type": "price_above", "symbol": "self", "value": 100},
    )
    assert expression is not None
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    fetched = client.get(f"/strategies/breakout/signals/{expression.id}")
    updated = client.patch(
        f"/strategies/breakout/signals/{expression.id}",
        json={
            "purpose": "filter",
            "expression": {"type": "price_below", "symbol": "self", "value": 200},
        },
    )
    deleted = client.delete(f"/strategies/breakout/signals/{expression.id}")

    assert fetched.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["purpose"] == "filter"
    assert deleted.json() == {"status": "deleted", "id": expression.id}
    event_types = {event.type for event in AuditEventStore(store.db_path).list(limit=10)}
    assert "signal_expression.updated" in event_types
    assert "signal_expression.deleted" in event_types


def test_api_deletes_only_disabled_unreferenced_strategy(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(id="unused", name="Unused")
    store.create(id="enabled", name="Enabled", enabled=True)
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    blocked = client.delete("/strategies/enabled")
    deleted = client.delete("/strategies/unused")

    assert blocked.status_code == 409
    assert deleted.json() == {"status": "deleted", "id": "unused"}
    assert store.get("unused") is None
    events = AuditEventStore(store.db_path).list(event_type="strategy.deleted")
    assert events[0].strategy_id == "unused"


def test_api_rejects_strategy_delete_with_unbackfilled_legacy_trade(monkeypatch, tmp_path):
    db_path = str(tmp_path / "strategies.db")
    store = StrategyStore(db_path)
    store.create(id="legacy", name="Legacy")
    trade_store = TradeStore(db_path)
    trade_store.save_trade(
        TradeRecord(
            id="legacy-trade",
            strategy_id="legacy",
            inst_id="BTC-USDT-SWAP",
            side="long",
            entry_price=100,
            status="closed",
        )
    )
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    client = TestClient(create_app(DaemonRunner()))

    response = client.delete("/strategies/legacy")

    assert response.status_code == 409
    assert store.get("legacy") is not None
    assert LogicalPositionStore(db_path).get("legacy-trade") is None


def test_strategy_mutation_rolls_back_when_audit_insert_fails(monkeypatch, tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    store.create(id="breakout", name="Before")
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: store)
    monkeypatch.setattr(
        AuditEventStore,
        "_save_on_connection",
        staticmethod(lambda connection, event: (_ for _ in ()).throw(OSError("disk full"))),
    )
    client = TestClient(create_app(DaemonRunner()), raise_server_exceptions=False)

    response = client.patch("/strategies/breakout", json={"expected_updated_at": store.get("breakout").updated_at, "name": "After"})

    assert response.status_code == 500
    assert store.get("breakout").name == "Before"


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
    position_store.save_protection(
        LogicalPositionProtection(
            position_id=trade.id,
            kind="attached_stop",
            algo_id="algo-api-a",
            algo_client_order_id="algo-client-api-a",
            quantity=0.1,
            stop_loss=2900,
        )
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
    assert logical["protection"]["status"] == "active"
    assert logical["protection"]["algo_id"] == "algo-api-a"
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


def test_api_groups_persisted_logical_positions(monkeypatch, tmp_path):
    position_store = LogicalPositionStore(str(tmp_path / "positions.db"))
    for position_id, quantity, entry_price in (("unit-a", 1.0, 100.0), ("unit-b", 2.0, 110.0)):
        position_store.save(
            LogicalPositionRecord(
                id=position_id,
                source="strategy",
                strategy_id="strategy-a",
                inst_id="BTC-USDT-SWAP",
                side="long",
                opened_quantity=quantity,
                remaining_quantity=quantity,
                entry_price=entry_price,
                status="open",
            )
        )
    monkeypatch.setattr(
        "src.api.app.LogicalPositionStore",
        lambda db_path=None: position_store,
    )
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/positions/groups?group_by=instrument_side")

    assert response.status_code == 200
    group = response.json()[0]
    assert group["position_ids"] == ["unit-a", "unit-b"]
    assert group["position_count"] == 2
    assert group["remaining_quantity"] == 3.0
    assert group["weighted_entry_price"] == 106.66666666666667


def test_api_group_limit_applies_after_complete_aggregation(monkeypatch, tmp_path):
    position_store = LogicalPositionStore(str(tmp_path / "positions.db"))
    for index in range(3):
        position_store.save(
            LogicalPositionRecord(
                id=f"unit-{index}",
                source="manual",
                inst_id="BTC-USDT-SWAP",
                side="long",
                opened_quantity=1,
                remaining_quantity=1,
                entry_price=100 + index,
                status="open",
            )
        )
    monkeypatch.setattr(
        "src.api.app.LogicalPositionStore",
        lambda db_path=None: position_store,
    )
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/positions/groups?limit=1")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["position_count"] == 3
    assert response.json()[0]["remaining_quantity"] == 3


def test_api_strategy_groups_split_financial_values_by_instrument_and_side(
    monkeypatch,
    tmp_path,
):
    position_store = LogicalPositionStore(str(tmp_path / "positions.db"))
    for position_id, inst_id, side, price in (
        ("btc", "BTC-USDT-SWAP", "long", 100),
        ("eth", "ETH-USDT-SWAP", "short", 200),
    ):
        position_store.save(
            LogicalPositionRecord(
                id=position_id,
                source="strategy",
                strategy_id="multi",
                inst_id=inst_id,
                side=side,
                opened_quantity=1,
                remaining_quantity=1,
                entry_price=price,
                status="open",
            )
        )
    monkeypatch.setattr(
        "src.api.app.LogicalPositionStore",
        lambda db_path=None: position_store,
    )
    client = TestClient(create_app(DaemonRunner()))

    response = client.get("/positions/groups?group_by=strategy")

    assert response.status_code == 200
    assert {(group["inst_id"], group["side"]) for group in response.json()} == {
        ("BTC-USDT-SWAP", "long"),
        ("ETH-USDT-SWAP", "short"),
    }
    assert {group["weighted_entry_price"] for group in response.json()} == {100, 200}


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
    event_types = {event.type for event in AuditEventStore(db_path).list(limit=10)}
    assert event_types >= {
        "position_close_condition.created",
        "position_close_condition.updated",
        "position_close_condition.deleted",
    }


def test_close_condition_mutation_rolls_back_when_audit_insert_fails(
    monkeypatch,
    tmp_path,
):
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
    monkeypatch.setattr(
        AuditEventStore,
        "_save_on_connection",
        staticmethod(lambda connection, event: (_ for _ in ()).throw(OSError("disk full"))),
    )
    client = TestClient(create_app(DaemonRunner()), raise_server_exceptions=False)

    response = client.post(
        "/positions/logical/unit-a/close-conditions",
        json={
            "purpose": "take_profit",
            "expression": {
                "type": "price_above",
                "symbol": "ETH-USDT-SWAP",
                "value": 3200,
            },
        },
    )

    assert response.status_code == 500
    assert position_store.list_close_conditions("unit-a") == []


def test_api_requires_confirmed_exchange_amend_for_owned_stop_edits(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(
        LogicalPositionRecord(
            id="protected-unit",
            source="manual",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=2,
            remaining_quantity=2,
            entry_price=3000,
            status="open",
        )
    )
    condition = position_store.create_close_condition(
        position_id="protected-unit",
        purpose="stop_loss",
        expression={
            "type": "price_below",
            "symbol": "ETH-USDT-SWAP",
            "value": 2900,
        },
    )
    position_store.save_protection(
        LogicalPositionProtection(
            position_id="protected-unit",
            kind="standalone_stop",
            algo_id="algo-1",
            algo_client_order_id="algo-client-1",
            quantity=2,
            stop_loss=2900,
        )
    )

    class AmendClient:
        def __init__(self):
            self.order = {
                "algoId": "algo-1",
                "algoClOrdId": "algo-client-1",
                "instId": "ETH-USDT-SWAP",
                "side": "sell",
                "ordType": "conditional",
                "state": "live",
                "posSide": "net",
                "reduceOnly": "true",
                "sz": "2",
                "slTriggerPx": "2900",
                "slOrdPx": "-1",
            }
            self.amendments = []

        def get_instruments(self, *, inst_type, inst_id):
            return [{
                "instId": inst_id,
                "state": "live",
                "minSz": "1",
                "lotSz": "1",
                "tickSz": "0.1",
            }]

        def get_pending_algo_orders(self, *, inst_id, ord_type="conditional"):
            return [self.order] if ord_type == "conditional" else []

        def get_ticker(self, *, inst_id):
            return [{"instId": inst_id, "last": "3100"}]

        def amend_position_stop(self, **kwargs):
            self.amendments.append(kwargs)
            self.order["sz"] = kwargs["sz"]
            self.order["slTriggerPx"] = kwargs["stop_trigger_px"]
            return {"algoId": self.order["algoId"], "sCode": "0"}

    exchange = AmendClient()
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.OKXClient", lambda: exchange)
    client = TestClient(create_app(DaemonRunner()))
    path = f"/positions/logical/protected-unit/close-conditions/{condition.id}"

    generic = client.patch(
        path,
        json={
            "expression": {
                "type": "price_below",
                "symbol": "ETH-USDT-SWAP",
                "value": 2850,
            }
        },
    )
    unconfirmed = client.post(
        "/positions/logical/protected-unit/protection/stop",
        json={
            "confirm": False,
            "condition_id": condition.id,
            "expression": {"type": "price_below", "symbol": "self", "value": 2850},
            "reason": "tighten operator stop",
        },
    )
    amended = client.post(
        "/positions/logical/protected-unit/protection/stop",
        json={
            "confirm": True,
            "condition_id": condition.id,
            "expression": {"type": "price_below", "symbol": "self", "value": 2850},
            "reason": "tighten operator stop",
        },
    )
    unconfirmed_break_even = client.post(
        "/positions/logical/protected-unit/break-even",
        json={
            "confirm": False,
            "condition_id": condition.id,
            "lock_in_pct": 0,
            "reason": "protect entry",
        },
    )
    break_even = client.post(
        "/positions/logical/protected-unit/break-even",
        json={
            "confirm": True,
            "condition_id": condition.id,
            "lock_in_pct": 0,
            "reason": "protect entry",
        },
    )

    assert generic.status_code == 409
    assert unconfirmed.status_code == 422
    assert amended.status_code == 200
    assert unconfirmed_break_even.status_code == 422
    assert break_even.status_code == 200
    assert break_even.json()["protection"]["status"] == "active"
    assert break_even.json()["protection"]["stop_loss"] == 3000
    assert break_even.json()["close_conditions"][0]["expression"]["value"] == 3000
    assert break_even.json()["close_conditions"][0]["metadata"]["break_even"]["status"] == "applied"
    assert exchange.amendments[0]["confirm"] is True
    assert len(exchange.amendments) == 2
    amend_events = AuditEventStore(db_path).list(
        event_type="position.protection_stop_amended",
        position_id="protected-unit",
    )
    assert len(amend_events) == 2
    assert any(event.payload.get("operation") == "break_even" for event in amend_events)


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
