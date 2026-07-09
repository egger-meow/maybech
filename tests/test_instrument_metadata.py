from fastapi.testclient import TestClient
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from src.api.app import create_app
from src.daemon.events import RuntimeState
from src.daemon.service import DaemonRunner
from src.daemon.account_service import AccountSnapshotService
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.instrument_metadata import InstrumentMetadataStore
from src.trading.instrument_sizing import InstrumentSizer


def _instrument(inst_id: str = "ETH-USDT-SWAP") -> dict[str, str]:
    return {
        "instId": inst_id,
        "instType": "SWAP",
        "state": "live",
        "baseCcy": "",
        "quoteCcy": "",
        "settleCcy": "USDT",
        "ctType": "linear",
        "ctVal": "0.1",
        "ctValCcy": "ETH",
        "ctMult": "1",
        "lotSz": "0.01",
        "minSz": "0.01",
        "tickSz": "0.01",
        "maxLmtSz": "100000",
        "maxMktSz": "1000",
    }


def test_instrument_cache_migrates_replaces_and_persists(tmp_path):
    db_path = str(tmp_path / "trades.db")
    store = InstrumentMetadataStore(db_path)

    saved = store.replace_type("SWAP", [_instrument()])
    reopened = InstrumentMetadataStore(db_path)

    assert store.applied_schema_versions() == [1, 2]
    assert saved[0].contract_value == "0.1"
    assert saved[0].contract_currency == "ETH"
    assert saved[0].size_precision == 2
    assert reopened.list()[0].inst_id == "ETH-USDT-SWAP"


def test_instrument_cache_migrates_existing_rows_to_okx_provenance(tmp_path):
    db_path = str(tmp_path / "trades.db")
    store = InstrumentMetadataStore(db_path)
    store.replace_type("SWAP", [_instrument()])
    with sqlite3.connect(db_path) as conn:
        conn.execute("ALTER TABLE instrument_metadata DROP COLUMN source")
        conn.execute(
            "DELETE FROM schema_migrations WHERE component = ? AND version = 2",
            ("instrument_metadata",),
        )

    migrated = InstrumentMetadataStore(db_path)

    assert migrated.applied_schema_versions() == [1, 2]
    assert migrated.list()[0].source == "okx"


def test_instrument_cache_rejects_incomplete_metadata_without_losing_cache(tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    store.replace_type("SWAP", [_instrument()])
    incomplete = _instrument("BTC-USDT-SWAP")
    incomplete["ctVal"] = ""
    incomplete["lotSz"] = ""

    try:
        store.replace_type("SWAP", [incomplete])
    except ValueError as exc:
        assert "lotSz" in str(exc)
    else:
        raise AssertionError("incomplete metadata must fail visibly")

    assert [item.inst_id for item in store.list()] == ["ETH-USDT-SWAP"]
    rejection = store.list_rejections()[0]
    assert rejection.inst_id == "BTC-USDT-SWAP"
    assert "lotSz" in rejection.error
    assert rejection.payload["ctVal"] == ""


def test_instrument_cache_isolates_malformed_rows_in_atomic_refresh(tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    malformed = _instrument("TESTING-USDT-SWAP")
    malformed["lotSz"] = ""

    saved = store.replace_type(
        "SWAP",
        [_instrument("BTC-USDT-SWAP"), malformed, _instrument("ETH-USDT-SWAP")],
    )

    assert [item.inst_id for item in saved] == ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    assert [item.inst_id for item in store.list_rejections()] == ["TESTING-USDT-SWAP"]

    store.replace_type("SWAP", [_instrument("SOL-USDT-SWAP")])
    assert [item.inst_id for item in store.list()] == ["SOL-USDT-SWAP"]
    assert store.list_rejections() == []


def test_instrument_cache_refreshes_only_when_daily_ttl_expires(tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))

    class Client:
        calls = 0

        def get_instruments(self, *, inst_type):
            self.calls += 1
            return [_instrument()]

    client = Client()
    first = store.refresh_if_stale(client)
    refreshed = datetime.fromisoformat(first[0].updated_at)
    fresh = store.refresh_if_stale(
        client,
        now=refreshed + timedelta(hours=23),
    )
    stale = store.refresh_if_stale(
        client,
        now=refreshed + timedelta(days=1, seconds=1),
    )

    assert client.calls == 2
    assert fresh[0].inst_id == "ETH-USDT-SWAP"
    assert stale[0].inst_id == "ETH-USDT-SWAP"
    assert store.cache_status(now=datetime.now(timezone.utc))["refresh_due_at"]


def test_exchange_refresh_replaces_fresh_simulation_metadata(tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    store.replace_type(
        "SWAP",
        [_instrument("BTC-USDT-SWAP")],
        source="simulation_builtin",
    )

    class Client:
        calls = 0

        def get_instruments(self, *, inst_type):
            self.calls += 1
            return [_instrument("ETH-USDT-SWAP")]

    client = Client()
    refreshed = store.refresh_if_stale(client)

    assert client.calls == 1
    assert [item.inst_id for item in refreshed] == ["ETH-USDT-SWAP"]
    assert refreshed[0].source == "okx"
    assert store.cache_status()["source"] == "okx"


def test_account_service_automatically_populates_instrument_cache(monkeypatch, tmp_path):
    db_path = str(tmp_path / "trades.db")

    class Client:
        calls = 0

        def get_instruments(self, *, inst_type):
            self.calls += 1
            return [_instrument()]

    class Dashboard:
        def __init__(self, client):
            self.client = client

        def get_account_summary(self):
            return {}

        def get_open_positions(self):
            return []

        def get_recent_trades(self, *, limit):
            return []

    client = Client()
    monkeypatch.setattr("src.daemon.account_service.OKXClient", lambda: client)
    monkeypatch.setattr("src.daemon.account_service.Dashboard", Dashboard)
    service = AccountSnapshotService(
        position_store=LogicalPositionStore(db_path),
    )

    service.setup()
    service.tick()

    assert client.calls == 1
    assert InstrumentMetadataStore(db_path).list()[0].inst_id == "ETH-USDT-SWAP"


def test_account_service_starts_when_unrelated_instrument_is_malformed(
    monkeypatch,
    tmp_path,
):
    db_path = str(tmp_path / "trades.db")
    malformed = _instrument("TESTING-USDT-SWAP")
    malformed["tickSz"] = ""

    class Client:
        def get_instruments(self, *, inst_type):
            assert inst_type == "SWAP"
            return [_instrument("BTC-USDT-SWAP"), malformed]

    class Dashboard:
        def __init__(self, client):
            self.client = client

    monkeypatch.setattr("src.daemon.account_service.OKXClient", Client)
    monkeypatch.setattr("src.daemon.account_service.Dashboard", Dashboard)
    service = AccountSnapshotService(position_store=LogicalPositionStore(db_path))

    service.setup()

    assert service.dashboard is not None
    assert [item.inst_id for item in InstrumentMetadataStore(db_path).list()] == [
        "BTC-USDT-SWAP"
    ]
    assert (
        InstrumentMetadataStore(db_path).list_rejections()[0].inst_id
        == "TESTING-USDT-SWAP"
    )


def test_instrument_api_exposes_cache_and_refresh_contract(monkeypatch, tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: store)

    class FakeOKXClient:
        def get_instruments(self, *, inst_type):
            assert inst_type == "SWAP"
            return [_instrument("BTC-USDT-SWAP"), _instrument()]

    monkeypatch.setattr("src.api.app.OKXClient", FakeOKXClient)
    client = TestClient(create_app(DaemonRunner(), api_token=""))

    missing = client.get("/instruments")
    refreshed = client.post("/instruments/refresh")
    cached = client.get("/instruments")

    assert missing.status_code == 503
    assert "cache is empty" in missing.json()["detail"]
    assert refreshed.status_code == 200
    assert [item["inst_id"] for item in refreshed.json()["items"]] == [
        "BTC-USDT-SWAP",
        "ETH-USDT-SWAP",
    ]
    assert refreshed.json()["rejected_items"] == []
    assert cached.json() == refreshed.json()


def test_instrument_api_exposes_rejected_rows_without_hiding_valid_catalog(
    monkeypatch,
    tmp_path,
):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    malformed = _instrument("TESTING-USDT-SWAP")
    malformed["minSz"] = ""
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: store)

    class FakeOKXClient:
        def get_instruments(self, *, inst_type):
            assert inst_type == "SWAP"
            return [_instrument("BTC-USDT-SWAP"), malformed]

    monkeypatch.setattr("src.api.app.OKXClient", FakeOKXClient)
    response = TestClient(create_app(DaemonRunner(), api_token="")).post(
        "/instruments/refresh"
    )

    assert response.status_code == 200
    assert [item["inst_id"] for item in response.json()["items"]] == ["BTC-USDT-SWAP"]
    assert response.json()["rejected_items"][0]["inst_id"] == "TESTING-USDT-SWAP"
    assert "minSz" in response.json()["rejected_items"][0]["error"]


def test_instrument_leverage_returns_max_configured_lever(monkeypatch):
    class FakeOKXClient:
        def get_leverage(self, *, inst_id, mgn_mode):
            assert inst_id == "ETH-USDT-SWAP"
            assert mgn_mode == "cross"
            return [
                {"instId": "ETH-USDT-SWAP", "mgnMode": "cross", "posSide": "net", "lever": "10"},
                {"instId": "BTC-USDT-SWAP", "mgnMode": "cross", "posSide": "net", "lever": "20"},
            ]

    monkeypatch.setattr("src.api.app.OKXClient", FakeOKXClient)
    response = TestClient(create_app(DaemonRunner(), api_token="")).get(
        "/instruments/ETH-USDT-SWAP/leverage"
    )

    assert response.status_code == 200
    assert response.json() == {
        "inst_id": "ETH-USDT-SWAP",
        "mgn_mode": "cross",
        "leverage": "10",
    }


def test_instrument_leverage_404s_when_okx_has_no_matching_row(monkeypatch):
    class FakeOKXClient:
        def get_leverage(self, *, inst_id, mgn_mode):
            return []

    monkeypatch.setattr("src.api.app.OKXClient", FakeOKXClient)
    response = TestClient(create_app(DaemonRunner(), api_token="")).get(
        "/instruments/ETH-USDT-SWAP/leverage"
    )

    assert response.status_code == 404


def test_instrument_leverage_blocked_in_simulation():
    runtime = RuntimeState()
    runtime.set_value("runtime.live_preflight", {"execution_mode": "simulation", "armed": False})
    response = TestClient(create_app(DaemonRunner(runtime), api_token="")).get(
        "/instruments/ETH-USDT-SWAP/leverage"
    )

    assert response.status_code == 409
    assert "Simulation" in response.json()["detail"]


def test_instrument_sizer_maps_base_quantity_to_contracts_and_pnl(tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    metadata = store.replace_type("SWAP", [_instrument()])[0]

    long_quote = InstrumentSizer(metadata).quote(
        display_quantity="0.25",
        entry_price="3000",
        side="long",
        rule_price="2900",
    )
    short_quote = InstrumentSizer(metadata).quote(
        display_quantity="0.25",
        entry_price="3000",
        side="short",
        rule_price="2900",
    )

    assert long_quote.to_dict()["api_quantity_contracts"] == "2.5"
    assert long_quote.to_dict()["estimated_notional_usdt"] == "750"
    assert long_quote.to_dict()["estimated_pnl_usdt"] == "-25"
    assert short_quote.to_dict()["estimated_pnl_usdt"] == "25"
    reversed_quote = InstrumentSizer(metadata).quote_contracts(
        api_quantity_contracts="2.5",
        entry_price="3000",
        side="long",
        rule_price="3100",
    )
    assert reversed_quote.to_dict()["display_quantity"] == "0.25"
    assert reversed_quote.to_dict()["estimated_pnl_usdt"] == "25"


def test_instrument_sizer_derives_fixed_loss_stop_and_chart_anchored_size(tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    metadata = store.replace_type("SWAP", [_instrument()])[0]
    sizer = InstrumentSizer(metadata)

    fixed = sizer.quote_risk(
        mode="fixed_loss",
        entry_price="3000",
        side="long",
        allowed_loss_usdt="25",
        position_notional_usdt="750",
    ).to_dict()
    anchored = sizer.quote_risk(
        mode="chart_anchored",
        entry_price="3000",
        side="long",
        allowed_loss_usdt="20",
        stop_price="2900",
        timeframe="15m",
        evidence={"level_price": 2900, "level_score": 0.8},
    ).to_dict()

    assert fixed["stop_price"] == "2906"
    assert fixed["estimated_loss_usdt"] == "25"
    assert fixed["price_loss_usdt"] == "23.5"
    assert fixed["modeled_cost_usdt"] == "1.5"
    assert fixed["stop_expression"] == {
        "type": "price_below", "symbol": "self", "value": 2906.0
    }
    assert anchored["estimated_notional_usdt"] == "564"
    assert anchored["api_quantity_contracts"] == "1.88"
    assert anchored["estimated_loss_usdt"] == "19.928"
    assert anchored["modeled_cost_usdt"] == "1.128"
    assert anchored["evidence"]["timeframe"] == "15m"
    assert anchored["evidence"]["level_score"] == 0.8


def test_risk_sizer_rounds_down_size_without_exceeding_allowed_loss(tmp_path):
    payload = _instrument()
    payload["lotSz"] = "1"
    payload["minSz"] = "1"
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    metadata = store.replace_type("SWAP", [payload])[0]

    quote = InstrumentSizer(metadata).quote_risk(
        mode="chart_anchored",
        entry_price="3000",
        side="long",
        allowed_loss_usdt="21",
        stop_price="2900",
    ).to_dict()

    assert quote["api_quantity_contracts"] == "1"
    assert quote["estimated_loss_usdt"] == "10.6"
    assert quote["unused_risk_usdt"] == "10.4"


def test_risk_sizer_rejects_when_cost_assumptions_consume_loss_budget(tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    metadata = store.replace_type("SWAP", [_instrument()])[0]
    sizer = InstrumentSizer(metadata)

    with pytest.raises(ValueError, match="consume the entire allowed loss"):
        sizer.quote_risk(
            mode="fixed_loss",
            entry_price="3000",
            side="long",
            allowed_loss_usdt="1",
            position_notional_usdt="1000",
            entry_fee_rate="0.001",
            exit_fee_rate="0.001",
            slippage_rate="0.001",
        )
    with pytest.raises(ValueError, match="between 0 and 0.02"):
        sizer.quote_risk(
            mode="chart_anchored",
            entry_price="3000",
            side="long",
            allowed_loss_usdt="20",
            stop_price="2900",
            slippage_rate="0.03",
        )


def test_risk_quote_api_returns_structured_stop_proposal(monkeypatch, tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: store)
    client = TestClient(create_app(DaemonRunner(), api_token=""))

    response = client.post(
        "/instruments/ETH-USDT-SWAP/risk-quote",
        json={
            "mode": "chart_anchored",
            "entry_price": "3000",
            "side": "long",
            "allowed_loss_usdt": "20",
            "stop_price": "2900",
            "timeframe": "15m",
            "evidence": {"level_kind": "support", "level_score": 0.8},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["estimated_notional_usdt"] == "564"
    assert body["estimated_loss_usdt"] == "19.928"
    assert body["modeled_cost_usdt"] == "1.128"
    assert body["stop_expression"]["type"] == "price_below"


def test_low_price_risk_quote_preserves_tick_scale_end_to_end(monkeypatch, tmp_path):
    payload = _instrument("XRP-USDT-SWAP")
    payload.update({
        "baseCcy": "XRP", "ctVal": "1", "ctValCcy": "XRP",
        "tickSz": "0.0001", "lotSz": "1", "minSz": "1",
    })
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    store.replace_type("SWAP", [payload])
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: store)
    client = TestClient(create_app(DaemonRunner(), api_token=""))

    response = client.post(
        "/instruments/XRP-USDT-SWAP/risk-quote",
        json={
            "mode": "chart_anchored", "entry_price": "0.1647", "side": "long",
            "allowed_loss_usdt": "10", "stop_price": "0.1593",
            "timeframe": "15m",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["entry_price"] == "0.1647"
    assert body["stop_price"] == "0.1593"
    assert body["stop_expression"]["value"] == pytest.approx(0.1593)


def test_instrument_sizer_handles_quote_denominated_contracts(tmp_path):
    payload = _instrument("BTC-USD-SWAP")
    payload.update({"settleCcy": "BTC", "ctVal": "100", "ctValCcy": "USD"})
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    metadata = store.replace_type("SWAP", [payload])[0]

    quote = InstrumentSizer(metadata).quote(
        display_quantity="0.02",
        entry_price="60000",
        side="long",
    )

    assert quote.to_dict()["api_quantity_contracts"] == "12"
    assert quote.to_dict()["estimated_notional_usdt"] == "1200"


def test_size_quote_api_blocks_missing_or_non_aligned_metadata(monkeypatch, tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    store.replace_type("SWAP", [_instrument()])
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: store)
    client = TestClient(create_app(DaemonRunner(), api_token=""))

    missing = client.post(
        "/instruments/BTC-USDT-SWAP/size-quote",
        json={"display_quantity": "0.01", "entry_price": "60000", "side": "long"},
    )
    invalid = client.post(
        "/instruments/ETH-USDT-SWAP/size-quote",
        json={"display_quantity": "0.0005", "entry_price": "3000", "side": "long"},
    )
    valid = client.post(
        "/instruments/ETH-USDT-SWAP/size-quote",
        json={
            "display_quantity": "0.25",
            "entry_price": "3000",
            "side": "short",
            "rule_price": "3100",
        },
    )

    assert missing.status_code == 404
    assert invalid.status_code == 409
    assert valid.status_code == 200
    assert valid.json()["api_quantity_contracts"] == "2.5"
    assert valid.json()["estimated_pnl_usdt"] == "-25"
    reversed_quote = client.post(
        "/instruments/ETH-USDT-SWAP/contract-quote",
        json={
            "api_quantity_contracts": "2.5",
            "entry_price": "3000",
            "side": "long",
            "rule_price": "2900",
        },
    )
    assert reversed_quote.status_code == 200
    assert reversed_quote.json()["display_quantity"] == "0.25"
    assert reversed_quote.json()["estimated_pnl_usdt"] == "-25"
