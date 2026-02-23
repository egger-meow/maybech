"""
Fluctuation feature for detecting rapid price changes.
"""

from typing import Any, Dict
import pandas as pd
from src.data.candle_miner.feature import Feature

class Fluctuation(Feature):
    """
    Measures the price fluctuation (percentage change) over the last `window` candles.
    """
    name = "fluctuation"

    def __init__(self, window: int = 15) -> None:
        """
        Parameters
        ----------
        window : int
            Number of candles to look back for calculating the fluctuation.
        """
        self.window = window

    def extract(self, candles: pd.DataFrame) -> Any:
        # Require enough candles
        if len(candles) < 2:
            return {}

        # If we have fewer candles than the window, we calculate against the oldest available
        lookback = min(self.window, len(candles) - 1)
        
        # Latest completed candle's closing price
        current_close = float(candles.iloc[-1]["close"])
        # Close price `lookback` candles ago
        old_close = float(candles.iloc[-(lookback + 1)]["close"])

        if old_close == 0:
            return {}

        pct_change = ((current_close - old_close) / old_close) * 100.0
        direction = "up" if pct_change > 0 else "down"

        return {
            "window_evaluated": lookback,
            "pct_change": pct_change,
            "direction": direction,
            "start_price": old_close,
            "end_price": current_close
        }
