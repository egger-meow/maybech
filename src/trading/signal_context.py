"""Helpers for building signal evaluation context from market candles."""

from __future__ import annotations

from typing import Any

import pandas as pd


_BAR_SECONDS: dict[str, int] = {
    "1s": 1,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1H": 3_600,
    "2H": 7_200,
    "4H": 14_400,
    "6H": 21_600,
    "12H": 43_200,
    "1D": 86_400,
    "1W": 604_800,
}


def bar_seconds(bar: str) -> int:
    return _BAR_SECONDS.get(bar, 60)


def collect_signal_requirements(expression: dict[str, Any]) -> dict[str, set]:
    """Collect symbols, rapid-move windows, and volume timeframes from JSON AST."""
    requirements = {
        "symbols": set(),
        "windows_seconds": set(),
        "timeframes": set(),
    }
    _collect_signal_requirements(expression, requirements)
    return requirements


def build_signal_context_from_candles(
    candles_by_symbol: dict[str, pd.DataFrame],
    *,
    bar: str = "1m",
    windows_seconds: set[int] | None = None,
) -> dict[str, Any]:
    """Convert candle frames into evaluator context.

    The returned shape is intentionally the same context consumed by
    SignalExpressionEngine: latest close prices, windowed percent changes, and
    latest-volume-vs-baseline ratios.
    """
    windows = windows_seconds or {60, 300, 600}
    prices: dict[str, float] = {}
    changes_pct: dict[str, float] = {}
    volume_ratios: dict[str, float] = {}
    source: dict[str, Any] = {
        "candles": {
            "available": False,
            "bar": bar,
            "symbols": {},
        }
    }

    step_seconds = bar_seconds(bar)
    for symbol, raw_df in candles_by_symbol.items():
        df = _sorted_numeric_candles(raw_df)
        source["candles"]["symbols"][symbol] = {"rows": int(len(df))}
        if df.empty:
            continue

        latest = df.iloc[-1]
        prices[symbol] = float(latest["close"])
        source["candles"]["available"] = True

        volume_ratio = _volume_ratio(df)
        if volume_ratio is not None:
            volume_ratios[f"{symbol}:{bar}"] = volume_ratio

        for window in windows:
            lookback = max(1, int(round(window / step_seconds)))
            if len(df) <= lookback:
                continue
            previous = float(df.iloc[-1 - lookback]["close"])
            if previous == 0:
                continue
            changes_pct[f"{symbol}:{int(window)}"] = ((float(latest["close"]) - previous) / previous) * 100.0

    return {
        "prices": prices,
        "changes_pct": changes_pct,
        "volume_ratios": volume_ratios,
        "source": source,
    }


def _collect_signal_requirements(expression: Any, requirements: dict[str, set]) -> None:
    if not isinstance(expression, dict):
        return

    if "op" in expression:
        conditions = expression.get("conditions")
        if isinstance(conditions, list):
            for condition in conditions:
                _collect_signal_requirements(condition, requirements)
        return

    symbol = expression.get("symbol")
    if isinstance(symbol, str) and symbol.strip():
        requirements["symbols"].add(symbol.strip())

    signal_type = expression.get("type")
    if signal_type in {"rapid_drop", "rapid_rise"}:
        try:
            requirements["windows_seconds"].add(int(float(expression["window_seconds"])))
        except (KeyError, TypeError, ValueError):
            pass
    if signal_type == "volume_multiple":
        timeframe = expression.get("timeframe")
        if isinstance(timeframe, str) and timeframe.strip():
            requirements["timeframes"].add(timeframe.strip())


def _sorted_numeric_candles(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "close" not in df.columns:
        return pd.DataFrame(columns=["close", "volume"])

    candles = df.copy()
    for column in ("close", "volume"):
        if column in candles.columns:
            candles[column] = pd.to_numeric(candles[column], errors="coerce")
    candles = candles.dropna(subset=["close"])
    if "timestamp" in candles.columns:
        candles = candles.sort_values("timestamp")
    candles = candles.reset_index(drop=True)
    return candles


def _volume_ratio(df: pd.DataFrame) -> float | None:
    if "volume" not in df.columns or len(df) < 2:
        return None
    baseline = df["volume"].iloc[max(0, len(df) - 21):-1].mean()
    latest = float(df.iloc[-1]["volume"])
    if pd.isna(baseline) or baseline <= 0:
        return None
    return latest / float(baseline)
