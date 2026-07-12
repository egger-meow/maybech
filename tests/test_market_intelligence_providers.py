import requests
import pytest

from src.market_intelligence.providers import alternative_me, bitcoin_data, coingecko, defillama, okx
from src.market_intelligence.providers.base import ProviderError, request_with_retry


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")

    def json(self):
        return self._payload


def test_request_with_retry_succeeds_after_transient_failure():
    calls = {"count": 0}

    def _fetch():
        calls["count"] += 1
        if calls["count"] < 2:
            raise requests.ConnectionError("boom")
        return "ok"

    assert request_with_retry(_fetch, retries=2, backoff_seconds=0.01) == "ok"
    assert calls["count"] == 2


def test_request_with_retry_raises_provider_error_after_exhausting_retries():
    def _fetch():
        raise requests.Timeout("slow")

    with pytest.raises(ProviderError) as exc_info:
        request_with_retry(_fetch, retries=1, backoff_seconds=0.01)
    assert exc_info.value.category == "timeout"


def test_alternative_me_provider_parses_observations(monkeypatch):
    payload = {
        "data": [
            {"value": "72", "value_classification": "Greed", "timestamp": "1700000200"},
            {"value": "60", "value_classification": "Greed", "timestamp": "1700000100"},
        ]
    }
    monkeypatch.setattr(alternative_me.requests, "get", lambda *a, **k: _FakeResponse(payload))

    provider = alternative_me.AlternativeMeProvider()
    observations = provider.fetch_observations()

    assert len(observations) == 2
    assert {obs.metric_id for obs in observations} == {"crypto_fear_greed"}
    values = {obs.value for obs in observations}
    assert values == {72.0, 60.0}


def test_alternative_me_provider_raises_when_payload_has_no_usable_items(monkeypatch):
    monkeypatch.setattr(alternative_me.requests, "get", lambda *a, **k: _FakeResponse({"data": []}))

    with pytest.raises(ValueError):
        alternative_me.AlternativeMeProvider().fetch_observations()


def test_alternative_me_provider_raises_provider_error_on_total_failure(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise requests.ConnectionError("boom")

    monkeypatch.setattr(alternative_me.requests, "get", _raise)

    with pytest.raises(ProviderError):
        alternative_me.AlternativeMeProvider().fetch_observations()


def _mvrv_get(zscore_payload=None, ratio_payload=None, fail_urls=()):
    def _get(url, *args, **kwargs):
        if url in fail_urls:
            raise requests.ConnectionError(f"boom: {url}")
        if url == bitcoin_data._ZSCORE_URL:
            return _FakeResponse(zscore_payload if zscore_payload is not None else {"unexpected": True})
        if url == bitcoin_data._RATIO_URL:
            return _FakeResponse(ratio_payload if ratio_payload is not None else {"unexpected": True})
        raise AssertionError(f"unexpected URL: {url}")

    return _get


def test_bitcoin_data_provider_parses_both_series(monkeypatch):
    monkeypatch.setattr(
        bitcoin_data.requests,
        "get",
        _mvrv_get(
            zscore_payload={
                "value": [
                    {"d": "2026-07-08", "mvrvZscore": 0.35},
                    {"d": "2026-07-09", "mvrvZscore": 0.32},
                ]
            },
            ratio_payload={
                "value": [
                    {"d": "2026-07-08", "mvrv": 1.42},
                    {"d": "2026-07-09", "mvrv": 1.38},
                ]
            },
        ),
    )

    observations = bitcoin_data.BitcoinDataMvrvProvider().fetch_observations()

    by_metric = {}
    for obs in observations:
        by_metric.setdefault(obs.metric_id, []).append(obs)
    assert {obs.value for obs in by_metric["btc_mvrv_z"]} == {0.35, 0.32}
    assert {obs.value for obs in by_metric["btc_mvrv"]} == {1.42, 1.38}
    assert all(obs.unit == "z_score" for obs in by_metric["btc_mvrv_z"])
    assert all(obs.unit == "ratio" for obs in by_metric["btc_mvrv"])


def test_bitcoin_data_provider_filters_nan_entries(monkeypatch):
    monkeypatch.setattr(
        bitcoin_data.requests,
        "get",
        _mvrv_get(
            zscore_payload={
                "value": [
                    {"d": "2026-07-07", "mvrvZscore": "NaN"},
                    {"d": "2026-07-08", "mvrvZscore": 0.35},
                ]
            }
        ),
    )

    observations = bitcoin_data.BitcoinDataMvrvProvider().fetch_observations()
    zscore_obs = [obs for obs in observations if obs.metric_id == "btc_mvrv_z"]

    assert len(zscore_obs) == 1
    assert zscore_obs[0].observed_at == "2026-07-08T00:00:00+00:00"


def test_bitcoin_data_provider_one_series_failing_does_not_drop_the_other(monkeypatch):
    monkeypatch.setattr(
        bitcoin_data.requests,
        "get",
        _mvrv_get(
            zscore_payload={"value": [{"d": "2026-07-09", "mvrvZscore": 0.32}]},
            fail_urls={bitcoin_data._RATIO_URL},
        ),
    )

    observations = bitcoin_data.BitcoinDataMvrvProvider().fetch_observations()

    assert len(observations) == 1
    assert observations[0].metric_id == "btc_mvrv_z"


def test_bitcoin_data_provider_raises_when_both_series_fail(monkeypatch):
    monkeypatch.setattr(bitcoin_data.requests, "get", _mvrv_get())

    with pytest.raises(ValueError):
        bitcoin_data.BitcoinDataMvrvProvider().fetch_observations()


def test_coingecko_provider_parses_all_four_metrics(monkeypatch):
    monkeypatch.setattr(
        coingecko.requests,
        "get",
        lambda *a, **k: _FakeResponse(
            {
                "data": {
                    "total_market_cap": {"usd": 2_000_000_000_000},
                    "total_volume": {"usd": 80_000_000_000},
                    "market_cap_percentage": {"btc": 56.2, "eth": 9.4},
                    "updated_at": 1783703892,
                }
            }
        ),
    )

    observations = coingecko.CoinGeckoGlobalProvider().fetch_observations()

    by_metric = {obs.metric_id: obs.value for obs in observations}
    assert by_metric == {
        "global_market_cap_usd": 2_000_000_000_000.0,
        "global_volume_24h_usd": 80_000_000_000.0,
        "btc_dominance_pct": 56.2,
        "eth_dominance_pct": 9.4,
    }
    assert all(obs.observed_at.startswith("2026-") for obs in observations)


def test_coingecko_provider_skips_missing_fields_and_raises_if_all_missing(monkeypatch):
    monkeypatch.setattr(coingecko.requests, "get", lambda *a, **k: _FakeResponse({"data": {}}))

    with pytest.raises(ValueError):
        coingecko.CoinGeckoGlobalProvider().fetch_observations()


def test_defillama_provider_sums_circulating_peggedusd(monkeypatch):
    payload = {
        "peggedAssets": [
            {"symbol": "USDT", "circulating": {"peggedUSD": 100.0}},
            {"symbol": "USDC", "circulating": {"peggedUSD": 50.0}},
            {"symbol": "BROKEN", "circulating": {}},
            {"symbol": "NOT_A_DICT", "circulating": "oops"},
        ]
    }
    monkeypatch.setattr(defillama.requests, "get", lambda *a, **k: _FakeResponse(payload))

    observations = defillama.DefiLlamaStablecoinProvider().fetch_observations()

    assert len(observations) == 1
    assert observations[0].metric_id == "stablecoin_total_mcap_usd"
    assert observations[0].value == 150.0
    assert observations[0].metadata["assets_counted"] == 2


def test_defillama_provider_raises_on_unexpected_shape(monkeypatch):
    monkeypatch.setattr(defillama.requests, "get", lambda *a, **k: _FakeResponse({"unexpected": True}))

    with pytest.raises(ValueError):
        defillama.DefiLlamaStablecoinProvider().fetch_observations()


def test_defillama_provider_raises_when_nothing_countable(monkeypatch):
    monkeypatch.setattr(
        defillama.requests, "get", lambda *a, **k: _FakeResponse({"peggedAssets": [{"symbol": "X"}]})
    )

    with pytest.raises(ValueError):
        defillama.DefiLlamaStablecoinProvider().fetch_observations()


class _FakeOKXClient:
    def __init__(self, tickers=None, funding=None, open_interest=None):
        self._tickers = tickers or {}
        self._funding = funding or {}
        self._open_interest = open_interest or {}

    def get_ticker(self, inst_id):
        return self._tickers.get(inst_id, [])

    def get_funding_rate(self, inst_id):
        return self._funding.get(inst_id, [])

    def get_open_interest(self, inst_id):
        return self._open_interest.get(inst_id, [])


def test_okx_provider_is_unconfigured_without_a_client():
    provider = okx.OKXMarketProvider(None)

    assert provider.is_configured() is False
    with pytest.raises(ProviderError) as exc_info:
        provider.fetch_observations()
    assert exc_info.value.category == "not_configured"


def test_okx_provider_parses_price_funding_and_oi():
    client = _FakeOKXClient(
        tickers={
            "BTC-USDT-SWAP": [{"last": "65000", "open24h": "64000"}],
            "ETH-USDT-SWAP": [{"last": "3200", "open24h": "3100"}],
        },
        funding={
            "BTC-USDT-SWAP": [{"fundingRate": "0.0002"}],
            "ETH-USDT-SWAP": [{"fundingRate": "0.0001"}],
        },
        open_interest={
            "BTC-USDT-SWAP": [{"oiCcy": "300"}],
            "ETH-USDT-SWAP": [{"oiCcy": "100"}],
        },
    )
    provider = okx.OKXMarketProvider(client)

    assert provider.is_configured() is True
    observations = provider.fetch_observations()

    by_metric = {obs.metric_id: obs.value for obs in observations}
    assert by_metric["okx_btc_price_usd"] == 65000.0
    assert by_metric["okx_eth_price_usd"] == 3200.0
    assert by_metric["okx_btc_funding_rate"] == 0.0002
    assert by_metric["okx_eth_funding_rate"] == 0.0001
    assert by_metric["okx_btc_oi_usd"] == 300.0
    assert by_metric["okx_eth_oi_usd"] == 100.0
    expected_weighted = (0.0002 * 300 + 0.0001 * 100) / 400
    assert round(by_metric["okx_oi_weighted_funding"], 8) == round(expected_weighted, 8)


def test_okx_provider_raises_when_everything_fails():
    client = _FakeOKXClient()  # empty responses for every symbol

    with pytest.raises(ValueError):
        okx.OKXMarketProvider(client).fetch_observations()
