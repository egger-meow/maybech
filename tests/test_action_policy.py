from src.strategies.base import Signal, TradeSetup
from src.trading.action_policy import BTCRegimeActionPolicy


def setup(signal: Signal) -> TradeSetup:
    return TradeSetup(
        signal=signal,
        entry_price=100.0,
        stop_loss=99.0,
        take_profit=102.0,
        reason="test setup",
    )


def test_policy_blocks_when_btc_regime_missing():
    decision = BTCRegimeActionPolicy().evaluate(
        pair="ETH-USDT-SWAP",
        setup=setup(Signal.LONG),
        btc_regime=None,
    )

    assert decision.allowed is False
    assert "unavailable" in decision.reason


def test_policy_blocks_long_during_strong_bearish_btc():
    decision = BTCRegimeActionPolicy().evaluate(
        pair="ETH-USDT-SWAP",
        setup=setup(Signal.LONG),
        btc_regime={"direction": "bearish", "strength": "strong", "impulse": "none"},
    )

    assert decision.allowed is False
    assert decision.btc_direction == "bearish"


def test_policy_blocks_short_during_btc_upside_impulse():
    decision = BTCRegimeActionPolicy().evaluate(
        pair="ETH-USDT-SWAP",
        setup=setup(Signal.SHORT),
        btc_regime={"direction": "neutral", "strength": "weak", "impulse": "up"},
    )

    assert decision.allowed is False
    assert decision.btc_impulse == "up"


def test_policy_allows_long_when_btc_supports_it():
    decision = BTCRegimeActionPolicy().evaluate(
        pair="ETH-USDT-SWAP",
        setup=setup(Signal.LONG),
        btc_regime={"direction": "bullish", "strength": "normal", "impulse": "up"},
    )

    assert decision.allowed is True
    assert decision.signal == "long"
