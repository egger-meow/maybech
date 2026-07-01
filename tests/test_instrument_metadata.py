from fastapi.testclient import TestClient
from datetime import datetime, timedelta, timezone

from src.api.app import create_app
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

    assert store.applied_schema_versions() == [1]
    assert saved[0].contract_value == "0.1"
    assert saved[0].contract_currency == "ETH"
    assert saved[0].size_precision == 2
    assert reopened.list()[0].inst_id == "ETH-USDT-SWAP"


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


def test_instrument_api_exposes_cache_and_refresh_contract(monkeypatch, tmp_path):
    store = InstrumentMetadataStore(str(tmp_path / "trades.db"))
    monkeypatch.setattr("src.api.app.InstrumentMetadataStore", lambda: store)

    class FakeOKXClient:
        def get_instruments(self, *, inst_type):
            assert inst_type == "SWAP"
            return [_instrument("BTC-USDT-SWAP"), _instrument()]

    monkeypatch.setattr("src.api.app.OKXClient", FakeOKXClient)
    client = TestClient(create_app(DaemonRunner()))

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
    assert cached.json() == refreshed.json()


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
    client = TestClient(create_app(DaemonRunner()))

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
