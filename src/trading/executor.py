"""
Order execution engine.

Receives TradeSetup from strategies and executes orders via the exchange client.
Also monitors open positions and triggers stop-loss / take-profit exits.
"""

from src.exchange.client import OKXClient
from src.strategies.base import TradeSetup


class Executor:
    """Manages live order placement and position lifecycle."""

    def __init__(self, client: OKXClient) -> None:
        self.client = client

    def execute(self, inst_id: str, setup: TradeSetup) -> dict:
        """Place entry order + set stop-loss and take-profit.

        Returns order response dict.
        """
        raise NotImplementedError

    def check_exits(self) -> list[dict]:
        """Check all open positions against their SL/TP levels.

        Returns list of closed position summaries.
        """
        raise NotImplementedError
