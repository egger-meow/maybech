"""
OHLCV candlestick fetching and caching.

Responsible for:
- Fetching historical candles from OKX via the exchange client
- Converting raw API data into pandas DataFrames
- Caching recent data to avoid redundant API calls
"""

from __future__ import annotations

import logging
import time

import pandas as pd

from src.exchange.client import OKXClient

logger = logging.getLogger(__name__)

# Column order matches OKX response: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
_RAW_COLUMNS = [
    "timestamp", "open", "high", "low", "close",
    "volume", "vol_ccy", "vol_ccy_quote", "confirm",
]

# Interval → milliseconds (used for pagination calculations)
_BAR_MS: dict[str, int] = {
    "1s": 1_000, "1m": 60_000, "3m": 180_000, "5m": 300_000,
    "15m": 900_000, "30m": 1_800_000, "1H": 3_600_000,
    "2H": 7_200_000, "4H": 14_400_000,
    "6H": 21_600_000, "12H": 43_200_000,
    "1D": 86_400_000, "1W": 604_800_000,
}


def _raw_to_df(raw: list[list]) -> pd.DataFrame:
    """Convert raw OKX candle arrays to a typed DataFrame."""
    if not raw:
        return pd.DataFrame(columns=_RAW_COLUMNS)

    df = pd.DataFrame(raw, columns=_RAW_COLUMNS)

    # Convert types
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(float), unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume", "vol_ccy", "vol_ccy_quote"):
        df[col] = df[col].astype(float)
    df["confirm"] = df["confirm"].astype(int)

    # Sort ascending by time (OKX returns newest first)
    df.sort_values("timestamp", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


class CandleManager:
    """Fetches, caches, and provides candlestick data."""

    def __init__(self, client: OKXClient) -> None:
        self.client = client
        self._cache: dict[str, pd.DataFrame] = {}

    def fetch(
        self,
        inst_id: str,
        bar: str = "1m",
        limit: int = 100,
    ) -> pd.DataFrame:
        """Fetch candles and return as DataFrame with columns:
        [timestamp, open, high, low, close, volume, ...].
        """
        raw = self.client.get_candles(
            inst_id=inst_id, bar=bar, limit=str(limit),
        )
        df = _raw_to_df(raw)
        cache_key = f"{inst_id}:{bar}"
        self._cache[cache_key] = df
        logger.info("Fetched %d candles for %s (%s)", len(df), inst_id, bar)
        return df

    def get_latest(self, inst_id: str, bar: str = "1m") -> pd.Series:
        """Get the most recent candle for a given instrument.

        Uses cache if available, otherwise fetches fresh data.
        """
        cache_key = f"{inst_id}:{bar}"
        if cache_key not in self._cache or self._cache[cache_key].empty:
            self.fetch(inst_id, bar, limit=10)

        df = self._cache[cache_key]
        return df.iloc[-1]

    def get_history(
        self,
        inst_id: str,
        bar: str = "1m",
        days: int = 30,
    ) -> pd.DataFrame:
        """Fetch extended historical data for backtesting.

        Paginates through the OKX history-candles endpoint to collect
        ``days`` worth of data, up to the API's limits.
        """
        bar_ms = _BAR_MS.get(bar, 60_000)
        total_candles = int((days * 86_400_000) / bar_ms)
        page_size = 100  # OKX max per request

        all_frames: list[pd.DataFrame] = []
        after = ""  # pagination cursor (oldest ts of previous batch)
        fetched = 0

        logger.info(
            "Fetching ~%d history candles for %s (%s, %d days)",
            total_candles, inst_id, bar, days,
        )

        while fetched < total_candles:
            remaining = min(page_size, total_candles - fetched)
            raw = self.client.get_history_candles(
                inst_id=inst_id, bar=bar,
                limit=str(remaining), after=after,
            )
            if not raw:
                logger.info("No more history data available, stopping.")
                break

            df_page = _raw_to_df(raw)
            all_frames.append(df_page)
            fetched += len(df_page)

            # OKX 'after' = return data older than this timestamp
            oldest_ts = df_page["timestamp"].iloc[0]
            after = str(int(oldest_ts.timestamp() * 1000))

            logger.debug(
                "Fetched page: %d candles (total %d / %d)",
                len(df_page), fetched, total_candles,
            )

            # Respect rate limits — 20 req/2s for history endpoint
            time.sleep(0.15)

        if not all_frames:
            return pd.DataFrame(columns=_RAW_COLUMNS)

        result = pd.concat(all_frames, ignore_index=True)
        result.sort_values("timestamp", inplace=True)
        result.drop_duplicates(subset="timestamp", inplace=True)
        result.reset_index(drop=True, inplace=True)

        logger.info("History: %d total candles for %s", len(result), inst_id)
        return result
