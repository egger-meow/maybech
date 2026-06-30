from fastapi.testclient import TestClient

from src.api.app import create_app
from src.daemon.events import RuntimeState
from src.daemon.service import DaemonRunner
from src.trading.audit_event_store import AuditEventStore
from src.trading.instrument_metadata import InstrumentMetadataStore
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.trade_store import TradeStore


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


def test_manual_open_rejects_live_unarmed_before_writing(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.LogicalPositionStore", lambda *_: position_store)
    runtime = RuntimeState()
    runtime.set_value(
        "runtime.live_preflight",
        {
            "execution_mode": "demo",
            "armed": False,
        },
    )

    response = TestClient(create_app(DaemonRunner(runtime))).post(
        "/positions/manual-open",
        json=_payload(),
    )

    assert response.status_code == 409
    assert "armed runtime" in response.json()["detail"]
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
