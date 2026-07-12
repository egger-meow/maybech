import requests
import pytest

from src.market_intelligence.providers import alternative_me, bitcoin_data, coingecko
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


def test_bitcoin_data_provider_parses_history(monkeypatch):
    monkeypatch.setattr(
        bitcoin_data.requests,
        "get",
        lambda *a, **k: _FakeResponse(
            {
                "value": [
                    {"d": "2026-07-08", "mvrvZscore": 0.35},
                    {"d": "2026-07-09", "mvrvZscore": 0.32},
                ]
            }
        ),
    )

    observations = bitcoin_data.BitcoinDataMvrvProvider().fetch_observations()

    assert len(observations) == 2
    assert observations[0].metric_id == "btc_mvrv_z"
    assert observations[-1].value == 0.32
    assert observations[-1].observed_at == "2026-07-09T00:00:00+00:00"


def test_bitcoin_data_provider_filters_nan_entries(monkeypatch):
    monkeypatch.setattr(
        bitcoin_data.requests,
        "get",
        lambda *a, **k: _FakeResponse(
            {
                "value": [
                    {"d": "2026-07-07", "mvrvZscore": "NaN"},
                    {"d": "2026-07-08", "mvrvZscore": 0.35},
                ]
            }
        ),
    )

    observations = bitcoin_data.BitcoinDataMvrvProvider().fetch_observations()

    assert len(observations) == 1
    assert observations[0].observed_at == "2026-07-08T00:00:00+00:00"


def test_bitcoin_data_provider_raises_on_unexpected_shape(monkeypatch):
    monkeypatch.setattr(bitcoin_data.requests, "get", lambda *a, **k: _FakeResponse({"unexpected": True}))

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
