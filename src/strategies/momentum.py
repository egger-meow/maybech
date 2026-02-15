"""
Momentum strategy — volume-spike based entry.

Core idea (from blueprint):
  If the current 15m candle has N times higher volume than the previous
  candle AND price is moving up → long. If price is moving down → short.
"""

import pandas as pd

from src.config.settings import settings
from src.strategies.base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    """Detects instant volume spikes and trades in the direction of the move."""

    name = "momentum"

    def __init__(
        self,
        volume_multiplier: float = 10.0,
        stop_loss_long_pct: float | None = None,
        stop_loss_short_pct: float | None = None,
        take_profit_long_pct: float | None = None,
        take_profit_short_pct: float | None = None,
    ) -> None:
        self.volume_multiplier = volume_multiplier
        # Bear-market bias defaults from settings
        self.sl_long = stop_loss_long_pct or settings.STOP_LOSS_LONG_PCT
        self.sl_short = stop_loss_short_pct or settings.STOP_LOSS_SHORT_PCT
        self.tp_long = take_profit_long_pct or settings.TAKE_PROFIT_LONG_PCT
        self.tp_short = take_profit_short_pct or settings.TAKE_PROFIT_SHORT_PCT

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Check for volume spike + price direction on the latest candle."""
        raise NotImplementedError

    def calc_stop_loss(self, entry_price: float, signal: Signal) -> float:
        """Asymmetric stop-loss: tighter for longs (bear-market bias)."""
        if signal == Signal.LONG:
            return entry_price * (1 - self.sl_long)
        return entry_price * (1 + self.sl_short)

    def calc_take_profit(self, entry_price: float, signal: Signal) -> float:
        """Asymmetric take-profit."""
        if signal == Signal.LONG:
            return entry_price * (1 + self.tp_long)
        return entry_price * (1 - self.tp_short)
