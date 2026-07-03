from fastapi.testclient import TestClient

from src.api.app import create_app
from src.daemon.service import DaemonRunner
from src.trading.audit_event_store import AuditEventStore
from src.trading.instrument_metadata import InstrumentMetadataStore
from src.trading.logical_position_store import LogicalPositionRecord, LogicalPositionStore
from src.trading.strategy_store import StrategyStore
from src.trading.trade_store import TradeStore


def _instrument() -> dict[str, str]:
    return {
        "instId": "ETH-USDT-SWAP", "instType": "SWAP", "state": "live",
        "baseCcy": "", "quoteCcy": "", "settleCcy": "USDT",
        "ctType": "linear", "ctVal": "0.1", "ctValCcy": "ETH",
        "ctMult": "1", "lotSz": "0.01", "minSz": "0.01",
        "tickSz": "0.01", "maxLmtSz": "100000", "maxMktSz": "1000",
    }


def _risk_payload(**extra):
    return {
        "confirm": True,
        "mode": "chart_anchored",
        "entry_price": "3000",
        "side": "long",
        "allowed_loss_usdt": "20",
        "stop_price": "2900",
        "timeframe": "15m",
        "evidence": {"level_kind": "support", "level_score": 0.8},
        **extra,
    }


def test_strategy_risk_stop_promotion_is_revision_bound_and_audited(monkeypatch, tmp_path):
    db_path = str(tmp_path / "promotion.db")
    strategy_store = StrategyStore(db_path)
    strategy = strategy_store.create(
        id="strategy", name="Strategy", target_instruments=["ETH-USDT-SWAP"]
    )
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.StrategyStore", lambda: strategy_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    client = TestClient(create_app(DaemonRunner(), api_token=""))

    blocked_research = client.post(
        "/strategies/strategy/risk-stop",
        json=_risk_payload(
            inst_id="ETH-USDT-SWAP",
            expected_updated_at=strategy.updated_at,
            evidence={
                "selected_research_level": 2900,
                "analysis_state": "stale",
                "level_state": "active",
                "btc_regime_alignment": "neutral",
            },
        ),
    )

    stale = client.post(
        "/strategies/strategy/risk-stop",
        json=_risk_payload(inst_id="ETH-USDT-SWAP", expected_updated_at="stale"),
    )
    response = client.post(
        "/strategies/strategy/risk-stop",
        json=_risk_payload(
            inst_id="ETH-USDT-SWAP", expected_updated_at=strategy.updated_at
        ),
    )

    assert blocked_research.status_code == 409
    assert blocked_research.json()["detail"]["state"] == "manual_review"
    assert stale.status_code == 409
    assert response.status_code == 200
    rule = response.json()["default_rules"]["close_conditions"][0]
    definition = rule["metadata"]["rule_definition"]
    assert rule["expression"]["value"] == 2900
    assert definition["schema_version"] == 1
    assert definition["evidence"]["promotion_target"] == "strategy_default"
    events = AuditEventStore(db_path).list(event_type="strategy.risk_stop_promoted")
    assert len(events) == 1


def test_position_risk_stop_promotion_requires_exact_existing_quantity(monkeypatch, tmp_path):
    db_path = str(tmp_path / "promotion.db")
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    position_store.save(LogicalPositionRecord(
        id="unit", source="manual", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=2, remaining_quantity=2, entry_price=3000, status="open",
    ))
    metadata_store = InstrumentMetadataStore(db_path)
    metadata_store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.TradeStore", lambda: trade_store)
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: metadata_store)
    client = TestClient(create_app(DaemonRunner(), api_token=""))
    revision = position_store.get("unit").updated_at

    response = client.post(
        "/positions/logical/unit/risk-stop",
        json=_risk_payload(expected_position_updated_at=revision),
    )
    wrong_size = client.post(
        "/positions/logical/unit/risk-stop",
        json=_risk_payload(
            expected_position_updated_at=revision,
            allowed_loss_usdt="30",
        ),
    )

    assert response.status_code == 200
    condition = response.json()["close_conditions"][0]
    assert condition["expression"]["value"] == 2900
    assert condition["metadata"]["rule_definition"]["evidence"]["promotion_target"] == "logical_position_override"
    assert wrong_size.status_code == 409
    events = AuditEventStore(db_path).list(event_type="position.risk_stop_promoted")
    assert len(events) == 1
