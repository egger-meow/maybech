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
        """Fetch candles and return as DataFrame. Merges with cache."""
        raw = self.client.get_candles(
            inst_id=inst_id, bar=bar, limit=str(limit),
        )
        df = _raw_to_df(raw)
        
        cache_key = f"{inst_id}:{bar}"
        if cache_key in self._cache:
            df = pd.concat([self._cache[cache_key], df], ignore_index=True)
            df.drop_duplicates(subset="timestamp", inplace=True)
            df.sort_values("timestamp", inplace=True)
            df.reset_index(drop=True, inplace=True)
        
        self._cache[cache_key] = df
        logger.info("Fetched %d candles for %s (%s). Cache size: %d", len(raw), inst_id, bar, len(df))
        return df

    def get_latest(self, inst_id: str, bar: str = "1m") -> pd.Series:
        """Get the most recent candle for a given instrument."""
        cache_key = f"{inst_id}:{bar}"
        # If cache is old or empty, fetch fresh
        self.fetch(inst_id, bar, limit=10)
        df = self._cache[cache_key]
        return df.iloc[-1]

    def get_history(
        self,
        inst_id: str,
        bar: str = "1m",
        days: int = 30,
    ) -> pd.DataFrame:
        """Fetch extended historical data with incremental caching.
        
        If requested data range is already in cache, returns it immediately.
        Otherwise, fetches only the missing part.
        """
        bar_ms = _BAR_MS.get(bar, 60_000)
        now_ts = pd.Timestamp.now(tz="UTC")
        start_ts = now_ts - pd.Timedelta(days=days)
        
        cache_key = f"{inst_id}:{bar}"
        cached_df = self._cache.get(cache_key, pd.DataFrame(columns=_RAW_COLUMNS))
        
        if not cached_df.empty:
            cache_start = cached_df["timestamp"].iloc[0]
            cache_end = cached_df["timestamp"].iloc[-1]
            
            # If cache covers the requested range [start_ts, now_ts]
            # (Give 5 mins buffer for 'now')
            if cache_start <= start_ts and cache_end >= (now_ts - pd.Timedelta(minutes=5)):
                logger.info("Cache hit for %d days of %s (%s)", days, inst_id, bar)
                return cached_df[cached_df["timestamp"] >= start_ts].copy()

        # Determine where to start fetching
        # OKX 'after' returns data older than the timestamp.
        # If we have cache, we might need to fetch data OLDER than cache or NEWER than cache.
        
        # Scenario A: Need data older than cache_start
        if not cached_df.empty and cache_start > start_ts:
            after = str(int(cache_start.timestamp() * 1000))
            logger.info("Fetching older history starting from %s", cache_start)
        else:
            after = "" # Start from now
            
        # Prepare for fetching
        page_size = 100
        all_frames: list[pd.DataFrame] = []
        fetched = 0

        logger.info(
            "Fetching missing history for %s (%s, %d days requested)",
            inst_id, bar, days,
        )
        while True:
            raw = self.client.get_history_candles(
                inst_id=inst_id, bar=bar,
                limit=str(page_size), after=after,
            )
            if not raw:
                break

            df_page = _raw_to_df(raw)
            all_frames.append(df_page)
            fetched += len(df_page)

            oldest_in_page = df_page["timestamp"].iloc[0]
            if oldest_in_page <= start_ts:
                # We have reached back far enough
                break
                
            after = str(int(oldest_in_page.timestamp() * 1000))
            time.sleep(0.15) # Rate limit safety

        # Scenario B: Cache is stale (cache_end < now)
        # We also need to fetch the gap from now down to cache_end.
        # This is handled by starting after="" if cache_end is too old.
        # To keep it simple, if cache is stale, we just start from "" and merge.
        if not cached_df.empty and cache_end < (now_ts - pd.Timedelta(minutes=5)):
             # Re-run loop from now
             after = ""
             while True:
                raw = self.client.get_history_candles(
                    inst_id=inst_id, bar=bar,
                    limit=str(page_size), after=after,
                )
                if not raw: break
                df_page = _raw_to_df(raw)
                all_frames.append(df_page)
                newest_in_page = df_page["timestamp"].iloc[-1]
                oldest_in_page = df_page["timestamp"].iloc[0]
                if oldest_in_page <= cache_end: break # Reached cache
                after = str(int(oldest_in_page.timestamp() * 1000))
                time.sleep(0.15)

        if all_frames:
            new_df = pd.concat(all_frames + [cached_df], ignore_index=True)
            new_df.drop_duplicates(subset="timestamp", inplace=True)
            new_df.sort_values("timestamp", inplace=True)
            new_df.reset_index(drop=True, inplace=True)
            self._cache[cache_key] = new_df
            cached_df = new_df

        logger.info("History: Returning %d candles for %s", len(cached_df[cached_df["timestamp"] >= start_ts]), inst_id)
        return cached_df[cached_df["timestamp"] >= start_ts].copy()
