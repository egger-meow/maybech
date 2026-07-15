from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from src.market.impulse_origin import find_impulse_origin


NOW = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)


def _client_for(frame: pd.DataFrame):
    class Client:
        def get_candles(self, *, inst_id, bar, limit):
            del inst_id, bar
            tail = frame.tail(int(limit))
            return [
                [
                    str(int(row.timestamp.timestamp() * 1000)),
                    str(row.open), str(row.high), str(row.low), str(row.close),
                    str(row.volume), str(row.volume), str(row.volume), "1",
                ]
                for row in tail.itertuples()
            ]
    return Client()


def _candles_with_impulses(*, burst_indexes: set[int], count: int = 80) -> pd.DataFrame:
    rows = []
    price = 100.0
    for index in range(count):
        if index in burst_indexes:
            open_price = price
            close = price * 1.05
            volume = 50.0
        else:
            open_price = price
            close = price * (1.001 if index % 2 == 0 else 0.999)
            volume = 10.0
        high = max(open_price, close) + 0.1
        low = min(open_price, close) - 0.1
        rows.append({
            "timestamp": NOW - timedelta(minutes=count - index),
            "open": open_price, "high": high, "low": low, "close": close,
            "volume": volume,
        })
        price = close
    return pd.DataFrame(rows)


def test_find_impulse_origin_resolves_most_recent_qualifying_candle_by_default():
    frame = _candles_with_impulses(burst_indexes={45, 60})
    result = find_impulse_origin(_client_for(frame), "ETH-USDT-SWAP", bar="1m", kind="bullish")

    assert result["price"] == result["raw_origin_price"]
    assert result["evidence"]["source"] == "impulse_origin"
    assert result["evidence"]["nth"] == 1
    assert result["evidence"]["candle_timestamp"] == (NOW - timedelta(minutes=80 - 60)).isoformat()
    assert result["evidence"]["qualifying_candidates"] == 2


def test_find_impulse_origin_selects_nth_occurrence():
    frame = _candles_with_impulses(burst_indexes={45, 60})
    result = find_impulse_origin(_client_for(frame), "ETH-USDT-SWAP", bar="1m", kind="bullish", nth=2)

    assert result["evidence"]["candle_timestamp"] == (NOW - timedelta(minutes=80 - 45)).isoformat()


def test_find_impulse_origin_applies_buffer_away_from_price_by_direction():
    bullish_frame = _candles_with_impulses(burst_indexes={60})
    bullish = find_impulse_origin(
        _client_for(bullish_frame), "ETH-USDT-SWAP", bar="1m", kind="bullish", buffer_pct=0.01,
    )
    assert bullish["price"] == pytest.approx(bullish["raw_origin_price"] * 0.99)

    bearish_rows = []
    price = 100.0
    for index in range(80):
        if index == 60:
            open_price = price
            close = price * 0.95
            volume = 50.0
        else:
            open_price = price
            close = price * (1.001 if index % 2 == 0 else 0.999)
            volume = 10.0
        high = max(open_price, close) + 0.1
        low = min(open_price, close) - 0.1
        bearish_rows.append({
            "timestamp": NOW - timedelta(minutes=80 - index),
            "open": open_price, "high": high, "low": low, "close": close,
            "volume": volume,
        })
        price = close
    bearish_frame = pd.DataFrame(bearish_rows)
    bearish = find_impulse_origin(
        _client_for(bearish_frame), "ETH-USDT-SWAP", bar="1m", kind="bearish", buffer_pct=0.01,
    )
    assert bearish["price"] == pytest.approx(bearish["raw_origin_price"] * 1.01)


def test_find_impulse_origin_raises_when_fewer_than_nth_qualifying_candles_exist():
    frame = _candles_with_impulses(burst_indexes={60})
    with pytest.raises(ValueError, match="only .* qualifying"):
        find_impulse_origin(_client_for(frame), "ETH-USDT-SWAP", bar="1m", kind="bullish", nth=2)


def test_find_impulse_origin_raises_when_thresholds_exclude_every_candle():
    frame = _candles_with_impulses(burst_indexes={60})
    with pytest.raises(ValueError, match="only 0 qualifying"):
        find_impulse_origin(
            _client_for(frame), "ETH-USDT-SWAP", bar="1m", kind="bullish", min_volume_multiple=49,
        )


def test_find_impulse_origin_raises_on_insufficient_history():
    frame = _candles_with_impulses(burst_indexes={5}, count=10)
    with pytest.raises(ValueError, match="insufficient candle history"):
        find_impulse_origin(_client_for(frame), "ETH-USDT-SWAP", bar="1m", kind="bullish")


def test_find_impulse_origin_rejects_invalid_parameters():
    frame = _candles_with_impulses(burst_indexes={60})
    client = _client_for(frame)
    with pytest.raises(ValueError, match="kind"):
        find_impulse_origin(client, "ETH-USDT-SWAP", bar="1m", kind="up")
    with pytest.raises(ValueError, match="nth"):
        find_impulse_origin(client, "ETH-USDT-SWAP", bar="1m", kind="bullish", nth=0)
    with pytest.raises(ValueError, match="min_volume_multiple"):
        find_impulse_origin(client, "ETH-USDT-SWAP", bar="1m", kind="bullish", min_volume_multiple=1)
    with pytest.raises(ValueError, match="min_body_ratio"):
        find_impulse_origin(client, "ETH-USDT-SWAP", bar="1m", kind="bullish", min_body_ratio=1.5)
    with pytest.raises(ValueError, match="min_body_vs_baseline_multiple"):
        find_impulse_origin(
            client, "ETH-USDT-SWAP", bar="1m", kind="bullish", min_body_vs_baseline_multiple=-1,
        )
    with pytest.raises(ValueError, match="buffer_pct"):
        find_impulse_origin(client, "ETH-USDT-SWAP", bar="1m", kind="bullish", buffer_pct=-0.1)
