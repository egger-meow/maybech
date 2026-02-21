"""
Notificator Service — monitors price proximity to support/resistance levels.
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from src.config.settings import settings
from src.config.notificator import NotificatorConfig
from src.data.candles import CandleManager
from src.data.candle_miner import CandleMiner, PeakValley
from src.exchange.client import OKXClient
from src.notifications.line_bot import LineBotNotifier
from src.daemon.service import DaemonService

logger = logging.getLogger(__name__)


class NotificatorService(DaemonService):
    """Monitors price proximity to significant PeakValley clusters."""

    name = "notificator"

    def __init__(self) -> None:
        super().__init__()
        self.config = NotificatorConfig.load()
        self.interval = float(self.config.check_interval)
        
        self.client = None
        self.candle_manager = None
        self.miner = None
        self.notifier = None
        
        # Cooldown tracking: (pair, timeframe, price) -> last_alert_time
        self.cooldowns: Dict[Tuple[str, str, float], datetime] = {}

    def setup(self) -> None:
        """Initialize components."""
        self.client = OKXClient()
        self.candle_manager = CandleManager(self.client)
        self.notifier = LineBotNotifier()
        
        # Initialize Miner with PeakValley
        self.miner = CandleMiner()
        self.miner.register(PeakValley(
            window=self.config.peak_valley_window,
            min_sharpness=0.0  # Keep all, filter by significance later
        ))
        
        logger.info(f"NotificatorService setup complete. Enabled: {self.config.enabled}")

    def tick(self) -> None:
        """Fetch levels, check current price, and notify if near."""
        # Reload config
        self.config = NotificatorConfig.load()
        self.interval = float(self.config.check_interval)
        
        if not self.config.enabled:
            return

        for pair in settings.TRADING_PAIRS:
            try:
                ticker = self.client.get_ticker(pair)
                if not ticker or not isinstance(ticker, list):
                    continue
                current_price = float(ticker[0]["last"])
                
                # 2. Check each timeframe
                for tf in self.config.timeframes:
                    self._check_timeframe(pair, tf, current_price)
            except Exception as e:
                logger.error(f"Error in NotificatorService tick for {pair}: {e}")

    def _check_timeframe(self, pair: str, tf: str, current_price: float) -> None:
        """Check proximity for a specific pair and timeframe."""
        # Fetch data for mining
        df = self.candle_manager.fetch(pair, tf, limit=self.config.candle_limit)
        if df.empty:
            return

        # Mine levels
        mined = self.miner.mine(df)
        pv_res = mined.get("peak_valley")
        if not pv_res or not pv_res.get("levels"):
            return

        # Get threshold for this pair
        threshold = self.config.proximity_thresholds.get(
            pair, 
            self.config.proximity_thresholds.get("DEFAULT", 50.0)
        )

        # Check each level
        for level in pv_res["levels"]:
            # Only consider significant levels
            if level["significance"] < self.config.min_significance:
                continue

            dist = abs(current_price - level["price"])
            if dist <= threshold:
                self._handle_proximity(pair, tf, current_price, level, dist)

    def _handle_proximity(self, pair: str, tf: str, current_price: float, level: dict, dist: float) -> None:
        """Check cooldown and send alert."""
        key = (pair, tf, level["price"])
        now = datetime.now()
        
        if key in self.cooldowns:
            last_alert = self.cooldowns[key]
            if now - last_alert < timedelta(minutes=self.config.cooldown_minutes):
                # Still in cooldown
                return

        # Send alert
        success = self.notifier.send_level_alert(
            inst_id=pair,
            timeframe=tf,
            price=current_price,
            kind=level["kind"],
            distance=dist,
            level_price=level["price"],
            significance=level["significance"],
            count=level["count"],
            purity=level["purity"]
        )
        
        if success:
            logger.info(f"Alert sent for {pair} {tf} @ {level['price']}")
            self.cooldowns[key] = now

    def teardown(self) -> None:
        logger.info("NotificatorService shutting down.")
