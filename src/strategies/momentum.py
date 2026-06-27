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

from src.config.strategy import StrategyConfig, MomentumConfig
from src.strategies.base import BaseStrategy, Signal


class MomentumStrategy(BaseStrategy):
    """Detects volume spikes and price gaps to trigger Swap trades."""

    name = "momentum_swap"

    def __init__(
        self,
        config: "MomentumConfig | None" = None,
        *,
        stop_loss_long_pct: float | None = None,
        stop_loss_short_pct: float | None = None,
        take_profit_long_pct: float | None = None,
        take_profit_short_pct: float | None = None,
    ) -> None:
        if config:
            self.config = config
        else:
            self.config = StrategyConfig.default().momentum
        
        # Shortcuts for logic
        self.k_long = self.config.k_long
        self.k_short = self.config.k_short
        self.gap_threshold = self.config.gap_threshold
        self.stop_loss_long_pct = (
            0.02 if stop_loss_long_pct is None else stop_loss_long_pct
        )
        self.stop_loss_short_pct = (
            0.04 if stop_loss_short_pct is None else stop_loss_short_pct
        )
        self.take_profit_long_pct = (
            0.03 if take_profit_long_pct is None else take_profit_long_pct
        )
        self.take_profit_short_pct = (
            0.05 if take_profit_short_pct is None else take_profit_short_pct
        )

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
        """Compute a percentage stop loss for the base strategy contract.

        Live momentum setups override this with previous-candle close context in
        create_setup(), but backtests and smoke tests still use this interface.
        """
        if signal == Signal.LONG:
            return entry_price * (1 - self.stop_loss_long_pct)
        if signal == Signal.SHORT:
            return entry_price * (1 + self.stop_loss_short_pct)
        return entry_price

    def calc_take_profit(self, entry_price: float, signal: Signal) -> float:
        if signal == Signal.LONG:
            return entry_price * (1 + self.take_profit_long_pct)
        if signal == Signal.SHORT:
            return entry_price * (1 - self.take_profit_short_pct)
        return entry_price

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

        # TP Calculation
        sl_dist = abs(entry_price - sl_price)
        
        # Base Ratio
        ratio = self.config.stop_win_ratio
        
        # Optional: Volume Scaling
        if self.config.stop_win_vol_ratio:
            k = self.k_long if signal == Signal.LONG else self.k_short
            
            # vol_scale = current_vol / (prev_vol * k)
            # using small epsilon for safety although prev_vol check exists
            prev_vol = prev["volume"] if prev["volume"] > 0 else 0.0001
            vol_scale = curr["volume"] / (prev_vol * k)
            
            ratio *= vol_scale

        dist = sl_dist * ratio

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
                f"Vol x{curr['volume']/(prev['volume'] if prev['volume']>0 else 0.0001):.1f} "
                f"> {self.k_long if signal==Signal.LONG else self.k_short} "
                f"& Gap {abs(entry_price - sl_price):.1f} > {self.gap_threshold} "
                f"| TP Ratio: {ratio:.2f}"
            ),
        )
