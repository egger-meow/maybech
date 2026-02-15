"""
Maybech — Crypto Auto-Trader
Entrypoint for the application.
"""

import argparse
import sys

from src.config.settings import settings
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Maybech crypto auto-trader")
    parser.add_argument(
        "--mode",
        choices=["live", "backtest"],
        default="backtest",
        help="Run mode: 'live' for real trading, 'backtest' for strategy validation",
    )
    parser.add_argument(
        "--strategy",
        default="momentum",
        help="Strategy to run (default: momentum)",
    )
    args = parser.parse_args()

    logger.info("Maybech starting in %s mode with strategy '%s'", args.mode, args.strategy)
    logger.info("Trading pairs: %s", settings.TRADING_PAIRS)
    logger.info("OKX demo mode: %s", "ON" if settings.OKX_FLAG == "1" else "OFF")

    # TODO: wire up strategy loading, backtest engine, and live executor
    logger.info("Setup complete — modules will be wired here.")


if __name__ == "__main__":
    main()
