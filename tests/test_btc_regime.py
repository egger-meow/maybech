import pandas as pd

from src.market.btc_regime import BTCRegimeAnalyzer


def make_candles(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "close": closes,
            "high": [price * 1.01 for price in closes],
            "low": [price * 0.99 for price in closes],
            "volume": [100.0 for _ in closes],
        }
    )


def test_btc_regime_detects_bullish_impulse():
    closes = [100 + i for i in range(59)] + [165]
    analyzer = BTCRegimeAnalyzer(
        impulse_threshold_pct=1.0,
        strong_trend_threshold_pct=0.3,
    )

    regime = analyzer.analyze(make_candles(closes))

    assert regime.direction == "bullish"
    assert regime.strength == "strong"
    assert regime.impulse == "up"
    assert regime.change_pct > 1.0
    assert regime.nearest_level is not None


def test_btc_regime_detects_bearish_impulse():
    closes = [200 - i for i in range(59)] + [120]
    analyzer = BTCRegimeAnalyzer(
        impulse_threshold_pct=1.0,
        strong_trend_threshold_pct=0.3,
    )

    regime = analyzer.analyze(make_candles(closes))

    assert regime.direction == "bearish"
    assert regime.strength == "strong"
    assert regime.impulse == "down"
    assert regime.change_pct < -1.0


def test_btc_regime_requires_enough_candles():
    analyzer = BTCRegimeAnalyzer()

    try:
        analyzer.analyze(make_candles([100, 101]))
    except ValueError as exc:
        assert "requires at least" in str(exc)
    else:
        raise AssertionError("Expected ValueError for insufficient candles")
