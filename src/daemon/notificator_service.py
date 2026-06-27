"""
Notificator Service — monitors price proximity to support/resistance levels.
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple

from src.config.strategy import StrategyConfig
from src.config.notificator import NotificatorConfig
from src.data.candles import CandleManager
from src.data.candle_miner import CandleMiner, PeakValley, Fluctuation
from src.exchange.client import OKXClient
from src.notifications.line_bot import LineBotNotifier
from src.daemon.service import DaemonService
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


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
        
        # Initialize Miner with features
        self.miner = CandleMiner()
        self.miner.register(PeakValley(
            window=self.config.features.peak_valley.window,
            min_sharpness=0.0  # Keep all, filter by significance later
        ))
        from src.data.candle_miner.fluctuation import Fluctuation
        self.miner.register(Fluctuation(
            window=self.config.features.fluctuation.window_minutes
        ))
        
        logger.info(f"NotificatorService setup complete. Enabled: {self.config.enabled}")

    def tick(self) -> None:
        """Fetch levels, check current price, and notify if near."""
        # Reload config
        self.config = NotificatorConfig.load()
        self.interval = float(self.config.check_interval)
        
        if not self.config.enabled:
            return

        for pair in StrategyConfig.load().target_instruments:
            try:
                ticker = self.client.get_ticker(pair)
                if not ticker or not isinstance(ticker, list):
                    continue
                current_price = float(ticker[0]["last"])
                
                # Check Peak Valley Timeframes
                if self.config.features.peak_valley.enabled:
                    for tf in self.config.features.peak_valley.timeframes:
                        self._check_peak_valley_timeframe(pair, tf, current_price)
                        
                # Check Fluctuation Timeframes
                if self.config.features.fluctuation.enabled:
                    for tf in self.config.features.fluctuation.timeframes:
                        self._check_fluctuation_timeframe(pair, tf)
            except Exception as e:
                logger.error(f"Error in NotificatorService tick for {pair}: {e}")

    def _check_peak_valley_timeframe(self, pair: str, tf: str, current_price: float) -> None:
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
        threshold = self.config.features.peak_valley.proximity_thresholds.get(
            pair, 
            self.config.features.peak_valley.proximity_thresholds.get("DEFAULT", 50.0)
        )

        # Check each level
        for level in pv_res["levels"]:
            # Only consider significant levels
            if level["significance"] < self.config.features.peak_valley.min_significance:
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
            if now - last_alert < timedelta(minutes=self.config.features.peak_valley.cooldown_minutes):
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
            self.publish_event(
                "notification.level_alert",
                {
                    "pair": pair,
                    "timeframe": tf,
                    "current_price": current_price,
                    "level_price": level["price"],
                    "kind": level["kind"],
                    "distance": dist,
                    "significance": level["significance"],
                    "count": level["count"],
                    "purity": level["purity"],
                },
            )

    def _check_fluctuation_timeframe(self, pair: str, tf: str) -> None:
        """Check for rapid price fluctuations."""
        # Need at least window_minutes + 1 candles to compare
        limit = self.config.features.fluctuation.window_minutes + 1
        df = self.candle_manager.fetch(pair, tf, limit=limit)
        if df.empty:
            return

        mined = self.miner.mine(df)
        fl_res = mined.get("fluctuation")
        if not fl_res:
            return

        pct_change = fl_res.get("pct_change", 0.0)
        direction = fl_res.get("direction", "up")
        
        # Get threshold
        threshold = self.config.features.fluctuation.thresholds_pct.get(
            pair,
            self.config.features.fluctuation.thresholds_pct.get("DEFAULT", 5.0)
        )

        if abs(pct_change) >= threshold:
            key = (pair, tf, "fluctuation")
            now = datetime.now()
            
            if key in self.cooldowns:
                last_alert = self.cooldowns[key]
                if now - last_alert < timedelta(minutes=self.config.features.fluctuation.cooldown_minutes):
                    return

            # Send alert
            success = self.notifier.send_fluctuation_alert(
                inst_id=pair,
                minutes=fl_res.get("window_evaluated", self.config.features.fluctuation.window_minutes),
                pct_change=pct_change,
                threshold=threshold,
                direction=direction,
                start_price=fl_res.get("start_price", 0.0),
                end_price=fl_res.get("end_price", 0.0)
            )

            if success:
                logger.info(f"Fluctuation alert sent for {pair} {tf}: {pct_change:+.2f}%")
                self.cooldowns[key] = now
                self.publish_event(
                    "notification.fluctuation_alert",
                    {
                        "pair": pair,
                        "timeframe": tf,
                        "pct_change": pct_change,
                        "threshold": threshold,
                        "direction": direction,
                        "start_price": fl_res.get("start_price", 0.0),
                        "end_price": fl_res.get("end_price", 0.0),
                    },
                )

    def teardown(self) -> None:
        logger.info("NotificatorService shutting down.")
