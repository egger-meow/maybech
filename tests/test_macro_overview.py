import requests

from src.market import macro_overview


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def setup_function(_function):
    macro_overview._cache.clear()


def test_fetch_fear_greed_returns_latest_and_ordered_history(monkeypatch):
    payload = {
        "data": [
            {"value": "72", "value_classification": "Greed", "timestamp": "1700000200"},
            {"value": "60", "value_classification": "Greed", "timestamp": "1700000100"},
        ]
    }
    monkeypatch.setattr(macro_overview.requests, "get", lambda *a, **k: _FakeResponse(payload))

    result = macro_overview.fetch_fear_greed()

    assert result["latest"]["value"] == 72
    assert [point["value"] for point in result["history"]] == [60, 72]
    assert "unavailable_reason" not in result


def test_fetch_fear_greed_reports_unavailable_on_error(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(macro_overview.requests, "get", _raise)

    result = macro_overview.fetch_fear_greed()

    assert result["latest"] is None
    assert result["history"] == []
    assert "unavailable_reason" in result


def test_fetch_fear_greed_serves_stale_cache_on_later_failure(monkeypatch):
    payload = {"data": [{"value": "50", "value_classification": "Neutral", "timestamp": "1700000000"}]}
    monkeypatch.setattr(macro_overview.requests, "get", lambda *a, **k: _FakeResponse(payload))
    first = macro_overview.fetch_fear_greed()
    assert first["latest"]["value"] == 50

    def _raise(*_args, **_kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(macro_overview.requests, "get", _raise)
    second = macro_overview.fetch_fear_greed()

    assert second == first


def test_classify_mvrv_buckets():
    assert macro_overview.classify_mvrv(None) is None
    assert macro_overview.classify_mvrv(-0.5) == "undervalued"
    assert macro_overview.classify_mvrv(1.0) == "neutral"
    assert macro_overview.classify_mvrv(3.0) == "elevated"
    assert macro_overview.classify_mvrv(7.0) == "overheated"


def test_fetch_mvrv_zscore_success(monkeypatch):
    monkeypatch.setattr(
        macro_overview.requests,
        "get",
        lambda *a, **k: _FakeResponse({"d": "2026-07-09", "mvrvZscore": 0.32}),
    )

    result = macro_overview.fetch_mvrv_zscore()

    assert result["value"] == 0.32
    assert result["as_of"] == "2026-07-09"
    assert result["classification"] == "neutral"


def test_fetch_mvrv_zscore_reports_unavailable_on_rate_limit(monkeypatch):
    monkeypatch.setattr(macro_overview.requests, "get", lambda *a, **k: _FakeResponse({}, status_code=429))

    result = macro_overview.fetch_mvrv_zscore()

    assert result["value"] is None
    assert result["classification"] is None
    assert "unavailable_reason" in result


class _FakeOKXClient:
    def __init__(self, tickers=None, funding=None, open_interest=None, raise_for=()):
        self._tickers = tickers or {}
        self._funding = funding or {}
        self._open_interest = open_interest or {}
        self._raise_for = set(raise_for)

    def get_ticker(self, inst_id):
        if inst_id in self._raise_for:
            raise RuntimeError("ticker failed")
        return self._tickers.get(inst_id, [])

    def get_funding_rate(self, inst_id):
        if inst_id in self._raise_for:
            raise RuntimeError("funding failed")
        return self._funding.get(inst_id, [])

    def get_open_interest(self, inst_id):
        if inst_id in self._raise_for:
            raise RuntimeError("oi failed")
        return self._open_interest.get(inst_id, [])


def test_fetch_prices_computes_change_pct():
    client = _FakeOKXClient(
        tickers={
            "BTC-USDT-SWAP": [{"last": "65000", "open24h": "64350"}],
            "ETH-USDT-SWAP": [{"last": "3200", "open24h": "3200"}],
        }
    )

    rows = macro_overview.fetch_prices(client, symbols=("BTC-USDT-SWAP", "ETH-USDT-SWAP"))

    btc, eth = rows
    assert btc["last_price"] == 65000.0
    assert round(btc["change_24h_pct"], 2) == 1.01
    assert eth["change_24h_pct"] == 0.0


def test_fetch_prices_degrades_one_bad_symbol():
    client = _FakeOKXClient(
        tickers={"ETH-USDT-SWAP": [{"last": "3200", "open24h": "3100"}]},
        raise_for={"BTC-USDT-SWAP"},
    )

    rows = macro_overview.fetch_prices(client, symbols=("BTC-USDT-SWAP", "ETH-USDT-SWAP"))

    assert rows[0]["last_price"] is None
    assert rows[1]["last_price"] == 3200.0


def test_fetch_funding_overview_weighted_average():
    client = _FakeOKXClient(
        funding={
            "BTC-USDT-SWAP": [{"fundingRate": "0.0002"}],
            "ETH-USDT-SWAP": [{"fundingRate": "0.0001"}],
        },
        open_interest={
            "BTC-USDT-SWAP": [{"oiCcy": "300"}],
            "ETH-USDT-SWAP": [{"oiCcy": "100"}],
        },
    )

    result = macro_overview.fetch_funding_overview(client, symbols=("BTC-USDT-SWAP", "ETH-USDT-SWAP"))

    assert len(result["entries"]) == 2
    expected = (0.0002 * 300 + 0.0001 * 100) / 400
    assert round(result["weighted_average_funding_rate"], 8) == round(expected, 8)


def test_fetch_funding_overview_unavailable_when_all_symbols_fail():
    client = _FakeOKXClient(raise_for={"BTC-USDT-SWAP", "ETH-USDT-SWAP"})

    result = macro_overview.fetch_funding_overview(client, symbols=("BTC-USDT-SWAP", "ETH-USDT-SWAP"))

    assert result["weighted_average_funding_rate"] is None
    assert "unavailable_reason" in result
