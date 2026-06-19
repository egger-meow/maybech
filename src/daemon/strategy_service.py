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
from src.strategies.base import Signal
from src.strategies.momentum import MomentumStrategy
from src.trading.action_policy import BTCRegimeActionPolicy
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
        self.action_policy = BTCRegimeActionPolicy()
        self.signals_history = []
        self.decisions_history = []

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
        self.strategy.k_long = config.momentum.k_long
        self.strategy.k_short = config.momentum.k_short
        self.strategy.gap_threshold = config.momentum.gap_threshold
        
        current_time = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
        status = {
            "status": "RUNNING",
            "last_update": current_time,
            "strategy": self.strategy.name,
            "dry_run": self.dry_run,
            "signals": self.signals_history[-10:],  # keep last 10 in status
            "decisions": self.decisions_history[-20:],
            "errors": []
        }

        for pair in settings.TRADING_PAIRS:
            try:
                # 1. Fetch latest candle
                bar = settings.CANDLE_INTERVAL
                df = self.candle_manager.fetch(pair, bar, limit=100)
                
                if df.empty:
                    logger.warning(f"No data for {pair}")
                    status["errors"].append(f"No data for {pair}")
                    continue
                
                # 2. Generate Signal
                signal = self.strategy.generate_signal(df)
                
                if signal != Signal.HOLD:
                    logger.info(f"Signal detected for {pair}: {signal}")
                    
                    # 3. Create Setup, evaluate BTC regime, then execute only if allowed.
                    setup = self.strategy.create_setup(df)
                    if setup:
                        btc_regime = None
                        if self.runtime is not None:
                            btc_regime = self.runtime.get_value("market.btc_regime")

                        decision = self.action_policy.evaluate(
                            pair=pair,
                            setup=setup,
                            btc_regime=btc_regime,
                        )
                        decision_entry = {
                            **decision.to_dict(),
                            "time": current_time,
                            "setup_reason": setup.reason,
                            "entry_price": setup.entry_price,
                            "stop_loss": setup.stop_loss,
                            "take_profit": setup.take_profit,
                        }
                        self.decisions_history.append(decision_entry)
                        status["decisions"] = self.decisions_history[-20:]
                        if self.runtime is not None:
                            self.runtime.set_value("strategy.decisions", status["decisions"])
                        self.publish_event("strategy.action_decision", decision_entry)

                        if not decision.allowed:
                            logger.info("Action blocked for %s: %s", pair, decision.reason)
                            status["signals"] = self.signals_history[-10:]
                            continue

                        # Execute
                        result = self.executor.execute(pair, setup)
                        sig_entry = {
                            "pair": pair,
                            "signal": setup.signal.value,
                            "price": setup.entry_price,
                            "time": current_time,
                            "result": "Order Placed" if result else "Failed",
                            "decision": decision.reason,
                        }
                        self.signals_history.append(sig_entry)
                        status["signals"] = self.signals_history[-10:]
                        self.publish_event("strategy.signal", sig_entry)
                    else:
                        logger.warning(f"Signal {signal} but setup creation failed for {pair}.")
                        self.publish_event(
                            "strategy.signal_rejected",
                            {
                                "pair": pair,
                                "signal": str(signal),
                                "time": current_time,
                                "reason": "setup creation failed",
                            },
                        )
            except Exception as e:
                logger.error(f"Error processing {pair} in StrategyService: {e}")
                status["errors"].append(f"Error in {pair}: {str(e)}")
                self.publish_event(
                    "strategy.error",
                    {"pair": pair, "time": current_time, "error": str(e)},
                )

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
