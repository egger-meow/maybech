"""
Backtest performance reporting & visualization.

Generates human-readable reports:
- Trade log (entry time, exit time, direction, PnL per trade)
- Equity curve plot
- Key metrics summary (win-rate, return, drawdown, Sharpe)
"""

from src.backtesting.engine import BacktestResult


class BacktestReport:
    """Generates visual and textual reports from backtest results."""

    def __init__(self, result: BacktestResult) -> None:
        self.result = result

    def print_summary(self) -> None:
        """Print key metrics to console."""
        raise NotImplementedError

    def plot_equity_curve(self, save_path: str | None = None) -> None:
        """Plot the equity curve using matplotlib.

        Args:
            save_path: If provided, save the figure to this path instead of showing.
        """
        raise NotImplementedError

    def export_trades_csv(self, path: str) -> None:
        """Export the full trade log to a CSV file."""
        raise NotImplementedError
