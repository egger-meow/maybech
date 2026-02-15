"""
OHLCV candlestick fetching and caching.

Responsible for:
- Fetching historical candles from OKX via the exchange client
- Converting raw API data into pandas DataFrames
- Caching recent data to avoid redundant API calls
"""

import pandas as pd

from src.exchange.client import OKXClient


class CandleManager:
    """Fetches, caches, and provides candlestick data."""

    def __init__(self, client: OKXClient) -> None:
        self.client = client
        self._cache: dict[str, pd.DataFrame] = {}

    def fetch(
        self,
        inst_id: str,
        bar: str = "15m",
        limit: int = 100,
    ) -> pd.DataFrame:
        """Fetch candles and return as DataFrame with columns:
        [timestamp, open, high, low, close, volume].
        """
        raise NotImplementedError

    def get_latest(self, inst_id: str, bar: str = "15m") -> pd.Series:
        """Get the most recent candle for a given instrument."""
        raise NotImplementedError

    def get_history(
        self,
        inst_id: str,
        bar: str = "15m",
        days: int = 30,
    ) -> pd.DataFrame:
        """Fetch extended historical data for backtesting."""
        raise NotImplementedError
