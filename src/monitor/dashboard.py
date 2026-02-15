"""
Account & position monitoring dashboard.

Provides a snapshot of:
- Current account balance (total equity, available balance)
- Open positions with unrealised PnL
- Recent trade history
"""

from src.exchange.client import OKXClient


class Dashboard:
    """Tracks account state and positions in real-time."""

    def __init__(self, client: OKXClient) -> None:
        self.client = client

    def get_account_summary(self) -> dict:
        """Return total equity, available balance, margin ratio, etc."""
        raise NotImplementedError

    def get_open_positions(self) -> list[dict]:
        """Return all open positions with unrealised PnL."""
        raise NotImplementedError

    def get_recent_trades(self, limit: int = 20) -> list[dict]:
        """Return recent closed trades for review."""
        raise NotImplementedError
