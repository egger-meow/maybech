from fastapi.testclient import TestClient

from src.api.app import create_app
from src.daemon.service import DaemonRunner
from src.trading.instrument_metadata import InstrumentMetadataStore


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
