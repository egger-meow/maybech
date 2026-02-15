"""
Momentum strategy — volume-spike based entry for Perpetual Swaps.

Logic (per blueprint):
  1. Compare Current Growing Candle (C) vs Last Closed Candle (P).
     (For backtesting: Compare Candle i vs Candle i-1).
  2. Volume Condition:
     - If Long: Vol(C) > K_LONG * Vol(P)
     - If Short: Vol(C) > K_SHORT * Vol(P)
  3. Price Gap Condition:
     - |Close(C) - Close(P)| > PRICE_GAP_THRESHOLD
  4. Direction:
     - Long if Close(C) > Close(P)
     - Short if Close(C) < Close(P)

Risk Management:
  - Stop Loss: Close(P)
  - Take Profit: Entry + (Entry - SL)  [1:1 ratio]
"""

import pandas as pd

from src.config.settings import settings
from src.config.strategy import StrategyConfig
from src.strategies.base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    """Detects volume spikes and price gaps to trigger Swap trades."""

    name = "momentum_swap"

    def __init__(self, config: StrategyConfig | None = None) -> None:
        if config:
            self.config = config
        else:
            self.config = StrategyConfig.load()
        
        # Shortcuts for logic
        self.k_long = self.config.k_long
        self.k_short = self.config.k_short
        self.gap_threshold = self.config.gap_threshold

    def generate_signal(self, df: pd.DataFrame) -> Signal:
        """Analyze the last two candles for a signal.

        Args:
            df: DataFrame with at least 2 rows.
                row[-1] is the Current (growing) candle.
                row[-2] is the Previous (closed) candle.
        """
        if len(df) < 2:
            return Signal.HOLD

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        # 1. Direction
        if curr["close"] > prev["close"]:
            direction = Signal.LONG
            k_factor = self.k_long
        elif curr["close"] < prev["close"]:
            direction = Signal.SHORT
            k_factor = self.k_short
        else:
            return Signal.HOLD

        # 2. Volume Spike
        # Avoid division by zero
        prev_vol = prev["volume"] if prev["volume"] > 0 else 0.0001
        vol_ratio = curr["volume"] / prev_vol

        if vol_ratio <= k_factor:
            return Signal.HOLD

        # 3. Price Gap
        price_gap = abs(curr["close"] - prev["close"])
        if price_gap <= self.gap_threshold:
            return Signal.HOLD

        return direction

    def calc_stop_loss(self, entry_price: float, signal: Signal) -> float:
        """stop_loss is the Close of the previous candle.

        Note: This method signature in BaseStrategy usually only takes entry/signal.
        However, this specific strategy depends on the *previous candle close* for SL.
        The BaseStrategy.create_setup structure might need a slight adjustment
        or we handle it by passing context.

        For now, since `create_setup` calls this, and `create_setup` has the DF,
        we should actually override `create_setup` in this class to access `prev['close']`.
        But to adhere to the base contract, we'll store the `sl_price` temporarily
        during `generate_signal` or just override `create_setup` entirely which is cleaner.
        """
        # This method is effectively unused if we override create_setup,
        # but required by abstract base class.
        return 0.0

    def calc_take_profit(self, entry_price: float, signal: Signal) -> float:
        # Unused if create_setup is overridden
        return 0.0

    def create_setup(self, df: pd.DataFrame) -> "TradeSetup | None":
        """Override to access previous candle for SL calculation."""
        from src.strategies.base import TradeSetup

        signal = self.generate_signal(df)
        if signal == Signal.HOLD:
            return None

        curr = df.iloc[-1]
        prev = df.iloc[-2]

        entry_price = float(curr["close"])
        sl_price = float(prev["close"])

        # TP is 1:1 distance
        dist = abs(entry_price - sl_price)

        if signal == Signal.LONG:
            tp_price = entry_price + dist
        else:
            tp_price = entry_price - dist

        return TradeSetup(
            signal=signal,
            entry_price=entry_price,
            stop_loss=sl_price,
            take_profit=tp_price,
            reason=(
                f"Vol x{curr['volume']/prev['volume']:.1f} > {self.k_long if signal==Signal.LONG else self.k_short} "
                f"& Gap {dist:.1f} > {self.gap_threshold}"
            ),
        )
