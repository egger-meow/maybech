"""
Market Data Aggregator.

Fetches global market metrics (BTC/ETH Price, Fear & Greed Index) and
calculates technical indicators (e.g. 200 EMA) for overview.
"""

import logging
import requests
from typing import Dict, Any

from src.data.candles import CandleManager

logger = logging.getLogger(__name__)


class MarketData:
    """Fetches and aggregates market overview data."""

    def __init__(self, candle_manager: CandleManager):
        self.candle_manager = candle_manager

    def get_crypto_prices(self) -> Dict[str, float]:
        """Fetch current prices."""
        prices = {}
        try:
            logger.debug("Fetching crypto prices...")
            client = self.candle_manager.client
            
            # Fetch BTC
            btc_list = client.get_ticker("BTC-USDT-SWAP")
            if btc_list and isinstance(btc_list, list) and len(btc_list) > 0:
                prices["BTC"] = float(btc_list[0]["last"])
            
            # Fetch ETH
            eth_list = client.get_ticker("ETH-USDT-SWAP")
            if eth_list and isinstance(eth_list, list) and len(eth_list) > 0:
                prices["ETH"] = float(eth_list[0]["last"])
                
            logger.debug(f"Fetched prices: {prices}")
        except Exception as e:
            logger.error(f"Failed to fetch crypto prices: {e}")
        
        return prices

    def get_fear_and_greed(self) -> Dict[str, Any]:
        """Fetch Fear & Greed from alternative.me."""
        try:
            resp = requests.get("https://api.alternative.me/fng/", timeout=5)
            data = resp.json()
            if "data" in data and len(data["data"]) > 0:
                item = data["data"][0]
                return {
                    "value": int(item["value"]),
                    "classification": item["value_classification"]
                }
        except Exception as e:
            logger.error(f"Failed to fetch Fear & Greed: {e}")
        
        return {"value": 0, "classification": "Unknown"}

    def get_200_ema(self, symbol: str = "BTC-USDT-SWAP") -> float:
        """Calculate 200-day EMA."""
        try:
            logger.debug(f"Calculating 200 EMA for {symbol}...")
            # Fetch last 300 days
            df = self.candle_manager.get_history(symbol, bar="1D", days=300)
            if df.empty or len(df) < 200:
                logger.warning(f"Not enough data for 200 EMA: {len(df)} candles")
                return 0.0
            
            ema = df["close"].ewm(span=200, adjust=False).mean()
            val = float(ema.iloc[-1])
            logger.debug(f"200 EMA: {val}")
            return val
            
        except Exception as e:
            logger.error(f"Failed to calc 200 EMA: {e}")
            return 0.0

    def get_mvrv_z_score(self) -> float:
        """Fetch MVRV Z-Score from Bitbo API."""
        try:
            logger.debug("Fetching MVRV Z-Score...")
            url = "https://charts.bitbo.io/api/v1/mvrv-z/?latest=true"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://charts.bitbo.io/",
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code == 200:
                # Returns list of points...
                data = resp.json()
                val = 0.0
                if isinstance(data, list) and len(data) > 0:
                    val = float(data[-1].get("z_score", 0.0))
                elif isinstance(data, dict):
                    val = float(data.get("z_score", 0.0))
                
                logger.debug(f"Fetched MVRV: {val}")
                return val
            else:
                logger.warning(f"MVRV fetch failed: {resp.status_code} {resp.text[:50]}")
            
        except Exception as e:
            logger.error(f"Failed to fetch MVRV: {e}")
        
        return 0.0
