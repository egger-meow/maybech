"""
Unit tests for Momentum Strategy logic.

Verifies:
1. Signal generation on specific volume/price patterns.
2. Direction accuracy (Long vs Short).
3. Parameter sensitivity (K-factor, Gap threshold).
"""

import pandas as pd
import pytest

from src.strategies.base import Signal
from src.strategies.momentum import MomentumStrategy


@pytest.fixture
def strategy():
    s = MomentumStrategy()
    s.k_long = 10.0
    s.k_short = 5.0
    s.gap_threshold = 3.0
    return s


def test_long_signal(strategy):
    """
    Long Condition:
    - Close(Current) > Close(Prev)
    - Vol(Current) > 10 * Vol(Prev)
    - |Close(Current) - Close(Prev)| > 3.0
    """
    df = pd.DataFrame([
        # Previous Candle: Vol=100, Close=2000
        {"close": 2000.0, "volume": 100.0},
        # Current Candle: Vol=1100 (11x), Close=2005 (Gap=5, Up)
        {"close": 2005.0, "volume": 1100.0},
    ])
    
    sig = strategy.generate_signal(df)
    assert sig == Signal.LONG


def test_short_signal(strategy):
    """
    Short Condition:
    - Close(Current) < Close(Prev)
    - Vol(Current) > 5 * Vol(Prev)
    - Gap > 3.0
    """
    df = pd.DataFrame([
        # Prev: Vol=100, Close=2000
        {"close": 2000.0, "volume": 100.0},
        # Curr: Vol=600 (6x), Close=1995 (Gap=5, Down)
        {"close": 1995.0, "volume": 600.0},
    ])
    
    sig = strategy.generate_signal(df)
    assert sig == Signal.SHORT


def test_hold_low_volume(strategy):
    """Volume check fails."""
    df = pd.DataFrame([
        {"close": 2000.0, "volume": 100.0},
        {"close": 2005.0, "volume": 900.0},  # 9x < 10x for Long
    ])
    assert strategy.generate_signal(df) == Signal.HOLD


def test_hold_small_gap(strategy):
    """Gap check fails."""
    df = pd.DataFrame([
        {"close": 2000.0, "volume": 100.0},
        {"close": 2002.0, "volume": 1100.0}, # Gap=2 < 3.0
    ])
    assert strategy.generate_signal(df) == Signal.HOLD


def test_hold_flat_price(strategy):
    """No price change = HOLD (avoids div/logic errors)."""
    df = pd.DataFrame([
        {"close": 2000.0, "volume": 100.0},
        {"close": 2000.0, "volume": 10000.0},
    ])
    assert strategy.generate_signal(df) == Signal.HOLD


def test_create_setup_sl_tp(strategy):
    """Verify overridden create_setup calculates SL/TP correctly."""
    df = pd.DataFrame([
        {"close": 2000.0, "volume": 100.0, "timestamp": 1},
        {"close": 2010.0, "volume": 1200.0, "timestamp": 2}, # Long, Gap=10
    ])
    
    setup = strategy.create_setup(df)
    assert setup is not None
    assert setup.signal == Signal.LONG
    assert setup.entry_price == 2010.0
    assert setup.stop_loss == 2000.0  # Prev Close
    assert setup.take_profit == 2020.0 # Entry + (Entry - SL) = 2010 + 10 = 2020
