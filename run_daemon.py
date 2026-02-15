"""
Daemon Runner.

The main loop that runs the strategy continuously in the background.
It fetches market data, generates signals, executes trades via Executor,
and writes its status to `data/daemon_status.json` for the TUI to display.
"""

import json
import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from src.config.settings import settings
from src.config.strategy import StrategyConfig
from src.data.candles import CandleManager
from src.exchange.client import OKXClient
from src.strategies.momentum import MomentumStrategy
from src.trading.executor import Executor
from src.utils.logger import setup_logger

# Configure logging
logger = setup_logger("daemon")

# Status file path
STATUS_FILE = Path("data/daemon_status.json")
STATUS_FILE.parent.mkdir(exist_ok=True)

TZ_TAIPEI = timezone(timedelta(hours=8))

def main():
    logger.info("Daemon started.")
    
    # Initialize components
    try:
        client = OKXClient()
        candle_manager = CandleManager(client)
        
        # Load Strategy with current config
        config = StrategyConfig.load()
        strategy = MomentumStrategy(config=config)
        
        # Executor (Default to Dry Run unless flag set?)
        # Let's use an env var or argument. For safety, default to DRY RUN.
        # User must explicitly disable dry run in code or env.
        # We'll use a simple constant for now, easy to change.
        DRY_RUN = True
        executor = Executor(client, dry_run=DRY_RUN)
        
        logger.info(f"Initialized components. Strategy: {strategy.name}. Dry Run: {DRY_RUN}")
        
    except Exception as e:
        logger.critical(f"Failed to initialize daemon: {e}")
        return

    # Main Loop
    while True:
        try:
            # reload config hot?
            config = StrategyConfig.load()
            strategy.config = config 
            
            current_time = datetime.now(TZ_TAIPEI).strftime("%Y-%m-%d %H:%M:%S")
            status = {
                "status": "RUNNING",
                "last_update": current_time,
                "strategy": strategy.name,
                "dry_run": DRY_RUN,
                "signals": [],
                "errors": []
            }

            for pair in settings.TRADING_PAIRS:
                # 1. Fetch latest candle
                # We need enough history for momentum (e.g. 50 candles)
                # bar assumed same as strategy interval
                bar = settings.CANDLE_INTERVAL
                df = candle_manager.get_history(pair, bar, limit=100) # Limit 100 is enough for vol calc
                
                if df.empty:
                    logger.warning(f"No data for {pair}")
                    status["errors"].append(f"No data for {pair}")
                    continue
                
                # 2. Generate Signal
                signal = strategy.generate_signal(df)
                
                if signal:
                    logger.info(f"Signal detected for {pair}: {signal}")
                    
                    # 3. Create Setup & Execute
                    setup = strategy.create_setup(df, signal)
                    if setup:
                        #Execute
                        result = executor.execute(pair, setup)
                        status["signals"].append({
                            "pair": pair,
                            "signal": str(signal),
                            "price": setup.entry_price,
                            "time": current_time,
                            "result": "Order Placed" if result else "Failed"
                        })
                    else:
                        logger.warning(f"Signal {signal} but setup creation failed.")
                else:
                    # No signal
                    pass

            # Write status to file
            with open(STATUS_FILE, "w") as f:
                json.dump(status, f, indent=4)
                
        except KeyboardInterrupt:
            logger.info("Daemon stopping...")
            break
        except Exception as e:
            logger.error(f"Daemon loop error: {e}")
            # Write error to status
            try:
                with open(STATUS_FILE, "w") as f:
                    json.dump({"status": "ERROR", "error": str(e), "last_update": datetime.now().isoformat()}, f)
            except:
                pass
        
        # Sleep
        time.sleep(10) # 10s poll interval

if __name__ == "__main__":
    main()
