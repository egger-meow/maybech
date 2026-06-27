import pandas as pd

from src.trading.signal_context import (
    build_signal_context_from_candles,
    collect_signal_requirements,
)


def test_collect_signal_requirements_from_composite_expression():
    requirements = collect_signal_requirements(
        {
            "op": "and",
            "conditions": [
                {"type": "price_above", "symbol": "BTC-USDT-SWAP", "value": 65000},
                {"type": "rapid_drop", "symbol": "ETH-USDT-SWAP", "window_seconds": 300, "change_pct": 2},
                {"type": "volume_multiple", "symbol": "ETH-USDT-SWAP", "timeframe": "1m", "multiplier": 2},
            ],
        }
    )

    assert requirements["symbols"] == {"BTC-USDT-SWAP", "ETH-USDT-SWAP"}
    assert requirements["windows_seconds"] == {300}
    assert requirements["timeframes"] == {"1m"}


def test_build_signal_context_from_candles_calculates_price_change_and_volume_ratio():
    df = pd.DataFrame(
        [
            {"timestamp": "2026-01-01T00:00:00Z", "close": 100, "volume": 10},
            {"timestamp": "2026-01-01T00:01:00Z", "close": 99, "volume": 10},
            {"timestamp": "2026-01-01T00:02:00Z", "close": 98, "volume": 10},
            {"timestamp": "2026-01-01T00:03:00Z", "close": 97, "volume": 10},
            {"timestamp": "2026-01-01T00:04:00Z", "close": 96, "volume": 10},
            {"timestamp": "2026-01-01T00:05:00Z", "close": 94, "volume": 30},
        ]
    )

    context = build_signal_context_from_candles(
        {"BTC-USDT-SWAP": df},
        bar="1m",
        windows_seconds={300},
    )

    assert context["prices"]["BTC-USDT-SWAP"] == 94.0
    assert context["changes_pct"]["BTC-USDT-SWAP:300"] == -6.0
    assert context["volume_ratios"]["BTC-USDT-SWAP:1m"] == 3.0
    assert context["source"]["candles"]["available"] is True
