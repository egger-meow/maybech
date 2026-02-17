"""
Maybech — Crypto Auto-Trader
Interactive Terminal UI (TUI) using Textual.
"""

import logging
from src.ui import MaybechApp
from src.utils.logger import setup_logger

# Configure logging
logger = setup_logger("")  # Configure root logger so all modules log
logging.getLogger("src.exchange.client").setLevel(logging.WARNING)
logging.getLogger("src.data.candles").setLevel(logging.WARNING)
logging.getLogger("src.backtesting.optimizer").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

if __name__ == "__main__":
    app = MaybechApp()
    app.run()
