"""
Abstract base strategy class.

Every strategy must implement:
1. generate_signal()  — decide long / short / hold
2. calc_stop_loss()   — compute stop-loss price for the entry
3. calc_take_profit() — compute take-profit price for the entry

This ensures the backtest engine and live executor can treat all
strategies uniformly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

import pandas as pd


class Signal(Enum):
    """Trading signal emitted by a strategy."""
    LONG = "long"
    SHORT = "short"
    HOLD = "hold"


@dataclass
class TradeSetup:
    """A complete trade setup produced by a strategy."""
    signal: Signal
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str = ""


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    name: str = "base"

    @abstractmethod
    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Analyse the latest candle data and return a trading signal.

        Args:
            df: OHLCV DataFrame with at least the most recent N candles.

        Returns:
            Signal.LONG, Signal.SHORT, or Signal.HOLD.
        """
        ...

    @abstractmethod
    def calc_stop_loss(self, entry_price: float, signal: Signal) -> float:
        """Compute the stop-loss price for a given entry.

        Should respect bear-market bias: tighter for longs, wider for shorts.
        """
        ...

    @abstractmethod
    def calc_take_profit(self, entry_price: float, signal: Signal) -> float:
        """Compute the take-profit price for a given entry."""
        ...

    def create_setup(self, df: pd.DataFrame) -> TradeSetup | None:
        """Full pipeline: signal → stop-loss → take-profit → TradeSetup.

        Returns None if signal is HOLD.
        """
        signal = self.generate_signal(df)
        if signal == Signal.HOLD:
            return None

        entry = df["close"].iloc[-1]
        return TradeSetup(
            signal=signal,
            entry_price=entry,
            stop_loss=self.calc_stop_loss(entry, signal),
            take_profit=self.calc_take_profit(entry, signal),
            reason=f"{self.name} strategy triggered",
        )
