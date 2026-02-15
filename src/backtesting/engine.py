"""
Core backtest runner.

Simulates the Momentum Strategy over historical data.
Validation Logic:
  - Entry at Close of candle `i`.
  - SL at Close of candle `i-1`.
  - TP at Entry + (Entry - SL) [1:1 Risk/Reward].
  - Check future candles `j > i`:
    - If Low <= SL: Stopped out (Loss).
    - If High >= TP: Take profit (Win).
    - Conservative assumption: If both SL and TP are hit in the same candle `j`,
      assume SL was hit first (Loss).
"""

import logging
from dataclasses import dataclass, field

import pandas as pd

from src.config.settings import settings
from src.data.candles import CandleManager
from src.strategies.base import BaseStrategy, Signal, TradeSetup

logger = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """Record of a simulated trade."""
    entry_time: pd.Timestamp
    entry_price: float
    signal: str  # "LONG" or "SHORT"
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str = ""  # "TP", "SL", "FORCE_CLOSE"
    pnl: float = 0.0
    is_win: bool = False


class BacktestResult:
    """Container for backtest output metrics."""

    def __init__(self) -> None:
        self.trades: list[BacktestTrade] = []
        self.total_trades: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.win_rate: float = 0.0
        
        # Extended Metrics
        self.total_pnl: float = 0.0
        self.avg_profit: float = 0.0
        self.avg_loss: float = 0.0
        self.max_win: float = 0.0
        self.max_loss: float = 0.0
        self.profit_factor: float = 0.0

    def add_trade(self, trade: BacktestTrade) -> None:
        self.trades.append(trade)
        self.total_trades += 1
        self.total_pnl += trade.pnl
        
        if trade.is_win:
            self.wins += 1
            self.max_win = max(self.max_win, trade.pnl)
        else:
            self.losses += 1
            self.max_loss = min(self.max_loss, trade.pnl)

        if self.total_trades > 0:
            self.win_rate = self.wins / self.total_trades
            
        # Calculate Averages and Profit Factor
        winning_trades = [t.pnl for t in self.trades if t.is_win]
        losing_trades = [t.pnl for t in self.trades if not t.is_win]
        
        self.avg_profit = sum(winning_trades) / len(winning_trades) if winning_trades else 0.0
        self.avg_loss = sum(losing_trades) / len(losing_trades) if losing_trades else 0.0
        
        gross_profit = sum(winning_trades)
        gross_loss = abs(sum(losing_trades))
        self.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0


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
        bar: str = "1m",
        days: int = 7,
    ) -> BacktestResult:
        """Execute the backtest simulation."""
        logger.info("Starting backtest for %s (%s, %d days)", inst_id, bar, days)

        # 1. Fetch History
        df = self.candle_manager.get_history(inst_id, bar, days=days)
        if df.empty or len(df) < 50:
            logger.warning("Not enough history data for backtest.")
            return BacktestResult()

        result = BacktestResult()
        
        # 2. Iterate Candles
        # Start from index 2 to have enough previous data for strategy
        # End before the very last candle to allow at least 1 future candle check
        for i in range(2, len(df) - 1):
            # Window: ... i-1, i (Current)
            # Strategy checks the last 2 rows of this slice
            window = df.iloc[i-2 : i+1]  # Slice includes i
            
            setup = self.strategy.create_setup(window)
            if not setup:
                continue

            # Simulate Trade Outcome
            trade = self._simulate_trade(setup, df, start_idx=i + 1)
            if trade:
                trade.entry_time = window.iloc[-1]["timestamp"]
                result.add_trade(trade)

        logger.info(
            "Backtest complete: %d trades, Win Rate: %.1f%%",
            result.total_trades, result.win_rate * 100,
        )
        return result

    def _simulate_trade(
        self,
        setup: TradeSetup,
        df: pd.DataFrame,
        start_idx: int,
    ) -> BacktestTrade | None:
        """Check future candles to see if TP or SL is hit first."""
        # Setup details
        is_long = (setup.signal == Signal.LONG)
        sl = setup.stop_loss
        tp = setup.take_profit
        
        trade = BacktestTrade(
            entry_time=pd.Timestamp.now(), # Placeholder, updated by caller
            entry_price=setup.entry_price,
            signal=setup.signal.name,
        )

        for j in range(start_idx, len(df)):
            candle = df.iloc[j]
            high = candle["high"]
            low = candle["low"]
            close = candle["close"]
            ts = candle["timestamp"]

            hit_sl = False
            hit_tp = False

            # Check if levels were reached in this candle
            if is_long:
                if low <= sl: hit_sl = True
                if high >= tp: hit_tp = True
            else: # SHORT
                if high >= sl: hit_sl = True
                if low <= tp: hit_tp = True

            # -- Conservative Outcome Logic --
            if hit_sl and hit_tp:
                # Conservative: Assume SL was hit first in a volatile candle
                trade.exit_price = sl
                trade.exit_time = ts
                trade.exit_reason = "SL (Conservative)"
                trade.is_win = False
                trade.pnl = sl - setup.entry_price if is_long else setup.entry_price - sl
                return trade

            if hit_sl:
                trade.exit_price = sl
                trade.exit_time = ts
                trade.exit_reason = "SL"
                trade.is_win = False
                trade.pnl = sl - setup.entry_price if is_long else setup.entry_price - sl
                return trade

            if hit_tp:
                trade.exit_price = tp
                trade.exit_time = ts
                trade.exit_reason = "TP"
                trade.is_win = True
                trade.pnl = tp - setup.entry_price if is_long else setup.entry_price - tp
                return trade

        # If we run out of data without hitting either
        return None
