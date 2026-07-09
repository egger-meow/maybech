from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.daemon.events import RuntimeState
from src.daemon.service import DaemonRunner
from src.trading.audit_event_store import AuditEventStore
from src.trading.account_risk import AccountRiskLimits, AccountRiskStore
from src.trading.instrument_metadata import InstrumentMetadataStore
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.trade_store import TradeStore


class FakeDemoExecutor:
    """Stands in for src.trading.executor.Executor in demo manual-open tests."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.dry_run = False

    def approve_entry(self, **kwargs):
        return FakeRiskApproval(kwargs)

    def execute(self, **kwargs):
        return {
            "ordId": "demo-order-1",
            "maybechRequestedSize": kwargs["requested_size"],
            "maybechProtectionVerified": True,
            "maybechProtection": {"active": None},
        }


class FakeRiskApproval:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return self.payload

    def matches(self, **_kwargs):
        return True


def _ready_execution_status() -> dict[str, object]:
    return {
        "service_available": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "caught_up": True,
        "websocket_enabled": True,
        "websocket_connected": True,
    }


def _instrument() -> dict[str, str]:
    return {
        "instId": "ETH-USDT-SWAP",
        "instType": "SWAP",
        "state": "live",
        "settleCcy": "USDT",
        "ctType": "linear",
        "ctVal": "0.1",
        "ctValCcy": "ETH",
        "ctMult": "1",
        "lotSz": "0.01",
        "minSz": "0.01",
        "tickSz": "0.01",
    }


def _payload() -> dict[str, object]:
    return {
        "confirm": True,
        "inst_id": "ETH-USDT-SWAP",
        "side": "short",
        "display_quantity": "0.25",
        "entry_price": "3000",
        "stop_loss_price": "3100",
        "take_profit_price": "2800",
    }


def test_manual_open_creates_source_tagged_simulated_unit(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {
            "execution_mode": "dry_run",
            "armed": False,
        },
    )

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "manual"
    assert body["strategy_id"] is None
    assert body["status"] == "open"
    assert body["opened_quantity"] == 2.5
    assert body["metadata"]["operator_display_quantity"] == "0.25"
    assert body["metadata"]["estimated_notional_usdt"] == "750"
    assert {rule["purpose"] for rule in body["close_conditions"]} == {
        "stop_loss",
        "take_profit",
    }
    events = AuditEventStore(db_path).list(position_id=body["id"])
    assert events[0].type == "position.manual_open_simulated"


def test_manual_open_rejects_live_safe_before_writing(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {
            "execution_mode": "live_safe",
            "armed": False,
        },
    )

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 409
    assert "Live Safe" in response.json()["detail"]
    assert position_store.list(status="all") == []


def test_manual_open_rejects_live_armed_when_not_armed(monkeypatch, tmp_path):
    """live_armed is only allowed through once the process is actually armed
    with order placement enabled — an unarmed live_armed preflight (e.g. failed
    preflight, or armed but entry_order_placement_enabled() still false) must
    still be rejected before writing anything."""
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {
            "execution_mode": "live_armed",
            "armed": False,
        },
    )

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 409
    assert "armed Live Armed runtime" in response.json()["detail"]
    assert position_store.list(status="all") == []


def test_manual_open_rejects_instrument_outside_account_allowlist(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    AccountRiskStore(db_path).save(AccountRiskLimits(
        enabled=True,
        max_order_notional_usd=1000,
        max_total_exposure_usd=5000,
        max_leverage=5,
        allowed_instruments=("BTC-USDT-SWAP",),
    ))
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {"execution_mode": "simulation", "armed": False},
    )

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 409
    assert "outside the account risk allowlist" in response.json()["detail"]
    assert position_store.list(status="all") == []


def test_manual_open_requires_confirmation_and_valid_stop(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    runtime = RuntimeState()
    runtime.set_value("runtime.live_preflight", {"execution_mode": "dry_run", "armed": False})
    client = TestClient(create_app(DaemonRunner(runtime)))
    payload = _payload()

    payload["confirm"] = False
    unconfirmed = client.post("/positions/manual-open", json=payload)
    payload.update({"confirm": True, "stop_loss_price": "2900"})
    unsafe_stop = client.post("/positions/manual-open", json=payload)

    assert unconfirmed.status_code == 422
    assert unsafe_stop.status_code == 409
    assert position_store.list(status="all") == []


def test_manual_open_demo_submits_real_order_via_shared_entry_path(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    monkeypatch.setattr("src.api.app.OKXClient", lambda: object())
    monkeypatch.setattr("src.api.app.Executor", FakeDemoExecutor)
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {"execution_mode": "demo", "armed": True},
    )
    runtime.set_value("execution.fills.status", _ready_execution_status())

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "manual"
    # Stays pending_open until a confirmed OKX fill arrives; a submitted real
    # order is not assumed filled, unlike the simulation fabrication path.
    assert body["status"] == "pending_open"
    assert body["exchange_order_id"] == "demo-order-1"
    events = AuditEventStore(db_path).list(position_id=body["id"])
    assert events[0].type == "position.manual_open_submitted"
    assert events[0].payload["order_id"] == "demo-order-1"


def test_manual_open_demo_blocked_until_execution_ingestion_ready(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    monkeypatch.setattr("src.api.app.OKXClient", lambda: object())
    monkeypatch.setattr("src.api.app.Executor", FakeDemoExecutor)
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {"execution_mode": "demo", "armed": True},
    )

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 409
    assert "execution catch-up" in response.json()["detail"]
    assert position_store.list(status="all") == []


def test_manual_open_demo_blocked_when_entries_not_enabled(monkeypatch, tmp_path):
    """Real Executor + no configured risk limits -> AccountRiskGuard blocks entries."""
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    monkeypatch.setattr("src.api.app.OKXClient", lambda: object())
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {"execution_mode": "demo", "armed": True},
    )
    runtime.set_value("execution.fills.status", _ready_execution_status())

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 409
    assert "account risk check" in response.json()["detail"]
    # The prepared trade/position intent is recovered to a terminal state, not
    # left dangling as pending_open.
    positions = position_store.list(status="all")
    assert len(positions) == 1
    assert positions[0].status == "failed"


def test_manual_open_live_armed_submits_real_order_via_shared_entry_path(monkeypatch, tmp_path):
    """Once the process is genuinely armed with order placement enabled, live_armed
    goes through the exact same guarded submission path as demo — same risk
    approval, execution-ingestion-readiness gate, and protection verification."""
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    monkeypatch.setattr("src.api.app.OKXClient", lambda: object())
    monkeypatch.setattr("src.api.app.Executor", FakeDemoExecutor)
    monkeypatch.setattr("src.api.app.entry_order_placement_enabled", lambda: True)
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {"execution_mode": "live_armed", "armed": True},
    )
    runtime.set_value("execution.fills.status", _ready_execution_status())

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "manual"
    assert body["status"] == "pending_open"
    assert body["exchange_order_id"] == "demo-order-1"
    events = AuditEventStore(db_path).list(position_id=body["id"])
    assert events[0].type == "position.manual_open_submitted"
    assert events[0].payload["execution_mode"] == "live_armed"


def test_manual_open_live_armed_blocked_until_execution_ingestion_ready(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    monkeypatch.setattr("src.api.app.OKXClient", lambda: object())
    monkeypatch.setattr("src.api.app.Executor", FakeDemoExecutor)
    monkeypatch.setattr("src.api.app.entry_order_placement_enabled", lambda: True)
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {"execution_mode": "live_armed", "armed": True},
    )

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 409
    assert "execution catch-up" in response.json()["detail"]
    assert position_store.list(status="all") == []


def test_manual_open_live_armed_blocked_when_entries_not_enabled(monkeypatch, tmp_path):
    """Real Executor + no configured risk limits -> AccountRiskGuard blocks entries,
    same operator-controlled entries_enabled gate the strategy path relies on."""
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    monkeypatch.setattr("src.api.app.OKXClient", lambda: object())
    monkeypatch.setattr("src.api.app.entry_order_placement_enabled", lambda: True)
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {"execution_mode": "live_armed", "armed": True},
    )
    runtime.set_value("execution.fills.status", _ready_execution_status())

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 409
    assert "account risk check" in response.json()["detail"]
    positions = position_store.list(status="all")
    assert len(positions) == 1
    assert positions[0].status == "failed"
