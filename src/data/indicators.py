"""
Technical indicator calculations.

Provides indicator functions that operate on pandas DataFrames
returned by CandleManager. Used by strategies for signal generation.
"""

import pandas as pd


def volume_spike(df: pd.DataFrame, multiplier: float = 10.0) -> pd.Series:
    """Detect candles where volume exceeds the previous candle by `multiplier` times.

    Returns a boolean Series.
    """
    raise NotImplementedError


def price_change_pct(df: pd.DataFrame) -> pd.Series:
    """Calculate percentage price change (close vs previous close).

    Returns a float Series.
    """
    raise NotImplementedError


def moving_average(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Simple moving average of the close price."""
    raise NotImplementedError


def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    raise NotImplementedError
