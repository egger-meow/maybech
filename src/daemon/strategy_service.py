"""
Strategy Executor Service — port of the original run_daemon.py logic.
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.config.settings import settings
from src.config.strategy import StrategyConfig
from src.data.candles import CandleManager
from src.exchange.client import OKXClient
from src.strategies.momentum import MomentumStrategy
from src.trading.executor import Executor
from src.daemon.service import DaemonService
from src.utils.logger import setup_logger

logger = setup_logger(__name__)

# Status file path for TUI compatibility
STATUS_FILE = Path("data/daemon_status.json")
TZ_TAIPEI = timezone(timedelta(hours=8))


class StrategyService(DaemonService):
    """Executes trading strategy on registered pairs."""

    name = "strategy"
    interval = 10.0  # 10 seconds

    def __init__(self, dry_run: bool = True) -> None:
        super().__init__()
        self.dry_run = dry_run
        self.client = None
        self.candle_manager = None
        self.strategy = None
        self.executor = None
        self.signals_history = []

    def setup(self) -> None:
        """Initialize exchange client and strategy components."""
        STATUS_FILE.parent.mkdir(exist_ok=True)
        
        self.client = OKXClient()
        self.candle_manager = CandleManager(self.client)
        
        # Load Strategy with current config. Specific momentum config is required.
        config = StrategyConfig.load()
        self.strategy = MomentumStrategy(config=config.momentum)
        
        # Executor
        self.executor = Executor(self.client, dry_run=self.dry_run)
        
        logger.info(
            f"StrategyService setup complete. Strategy: {self.strategy.name}. "
            f"Dry Run: {self.dry_run}"
        )

    def tick(self) -> None:
        """Fetch data, generate signals, and execute trades."""
        # Reload config hot
        config = StrategyConfig.load()
        self.strategy.config = config.momentum
        
        current_time = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
        status = {
            "status": "RUNNING",
            "last_update": current_time,
            "strategy": self.strategy.name,
            "dry_run": self.dry_run,
            "signals": self.signals_history[-10:],  # keep last 10 in status
            "errors": []
        }

        for pair in settings.TRADING_PAIRS:
            try:
                # 1. Fetch latest candle
                bar = settings.CANDLE_INTERVAL
                df = self.candle_manager.get_history(pair, bar, limit=100)
                
                if df.empty:
                    logger.warning(f"No data for {pair}")
                    status["errors"].append(f"No data for {pair}")
                    continue
                
                # 2. Generate Signal
                signal = self.strategy.generate_signal(df)
                
                if signal:
                    logger.info(f"Signal detected for {pair}: {signal}")
                    
                    # 3. Create Setup & Execute
                    setup = self.strategy.create_setup(df, signal)
                    if setup:
                        # Execute
                        result = self.executor.execute(pair, setup)
                        sig_entry = {
                            "pair": pair,
                            "signal": str(signal),
                            "price": setup.entry_price,
                            "time": current_time,
                            "result": "Order Placed" if result else "Failed"
                        }
                        self.signals_history.append(sig_entry)
                        status["signals"] = self.signals_history[-10:]
                    else:
                        logger.warning(f"Signal {signal} but setup creation failed for {pair}.")
            except Exception as e:
                logger.error(f"Error processing {pair} in StrategyService: {e}")
                status["errors"].append(f"Error in {pair}: {str(e)}")

        # Write status to file for TUI
        try:
            with open(STATUS_FILE, "w") as f:
                json.dump(status, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to write strategy status file: {e}")

    def teardown(self) -> None:
        """Write stop status."""
        logger.info("StrategyService shutting down.")
        try:
            if STATUS_FILE.exists():
                with open(STATUS_FILE, "r") as f:
                    data = json.load(f)
                data["status"] = "STOPPED"
                data["last_update"] = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
                with open(STATUS_FILE, "w") as f:
                    json.dump(data, f, indent=4)
        except:
            pass
