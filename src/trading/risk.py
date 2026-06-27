"""
Risk management module.

Responsibilities:
- Position sizing based on an explicit risk policy
- Bear-market bias enforcement (asymmetric SL for long vs short)
- Live performance monitoring — auto-stop strategy if metrics degrade
"""

from src.strategies.base import Signal


class RiskManager:
    """Enforces risk rules before and during trades."""

    def calc_position_size(self, balance: float, signal: Signal) -> float:
        """Calculate the order size in USDT.

        The concrete policy must define its sizing limits explicitly.
        """
        raise NotImplementedError

    def should_stop_strategy(
        self,
        recent_win_rate: float,
        recent_return: float,
    ) -> bool:
        """Check if a live strategy's performance has degraded below thresholds.

        If True, the strategy should be paused and re-backtested.
        """
        raise NotImplementedError
