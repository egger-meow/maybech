"""
Core backtest runner.

Simulates a strategy over historical candle data (fetched via the data module)
and produces performance metrics. A strategy must pass configured thresholds
(win-rate, return-rate) before being approved for live trading.
"""

import pandas as pd

from src.config.settings import settings
from src.data.candles import CandleManager
from src.strategies.base import BaseStrategy


class BacktestResult:
    """Container for backtest output metrics."""

    def __init__(self) -> None:
        self.trades: list[dict] = []
        self.total_return: float = 0.0
        self.win_rate: float = 0.0
        self.max_drawdown: float = 0.0
        self.sharpe_ratio: float = 0.0

    @property
    def passed(self) -> bool:
        """Check if results meet minimum thresholds for live deployment."""
        return (
            self.win_rate >= settings.BACKTEST_MIN_WIN_RATE
            and self.total_return >= settings.BACKTEST_MIN_RETURN_RATE
        )


class BacktestEngine:
    """Runs a strategy against historical data and collects results."""

    def __init__(
        self,
        strategy: BaseStrategy,
        candle_manager: CandleManager,
    ) -> None:
        self.strategy = strategy
        self.candle_manager = candle_manager

    def run(
        self,
        inst_id: str,
        bar: str = "15m",
        lookback_days: int | None = None,
    ) -> BacktestResult:
        """Execute the backtest.

        Steps:
        1. Fetch historical candles via candle_manager (data module)
        2. Iterate candles, call strategy.create_setup() on each window
        3. Simulate entries, stop-loss, take-profit exits
        4. Compute aggregate metrics

        Returns:
            BacktestResult with trades list and performance metrics.
        """
        raise NotImplementedError
