"""
Smoke test — verifies the basic structure is importable.

Run: pytest tests/
"""

from src.strategies.base import BaseStrategy, Signal, TradeSetup
from src.strategies.momentum import MomentumStrategy


def test_signal_enum():
    """Signal enum has the expected values."""
    assert Signal.LONG.value == "long"
    assert Signal.SHORT.value == "short"
    assert Signal.HOLD.value == "hold"


def test_momentum_strategy_is_base_strategy():
    """MomentumStrategy inherits from BaseStrategy."""
    assert issubclass(MomentumStrategy, BaseStrategy)


def test_momentum_stop_loss_bear_bias():
    """Stop-loss is tighter for longs than shorts (bear-market bias)."""
    strategy = MomentumStrategy(
        stop_loss_long_pct=0.02,
        stop_loss_short_pct=0.04,
    )
    entry = 100.0
    sl_long = strategy.calc_stop_loss(entry, Signal.LONG)
    sl_short = strategy.calc_stop_loss(entry, Signal.SHORT)

    # Long SL is 2% below → 98.0
    assert sl_long == 98.0
    # Short SL is 4% above → 104.0
    assert sl_short == 104.0
    # The long SL distance (2.0) is tighter than short SL distance (4.0)
    assert abs(entry - sl_long) < abs(sl_short - entry)


def test_trade_setup_dataclass():
    """TradeSetup can be constructed with expected fields."""
    setup = TradeSetup(
        signal=Signal.LONG,
        entry_price=50000.0,
        stop_loss=49000.0,
        take_profit=51500.0,
        reason="test",
    )
    assert setup.signal == Signal.LONG
    assert setup.entry_price == 50000.0
