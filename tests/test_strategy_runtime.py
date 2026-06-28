from src.trading.strategy_runtime import (
    close_condition_specs,
    compose_entry_expression,
    entry_limit_price,
    resolve_self_symbol,
    validate_strategy_for_execution,
)
from src.trading.strategy_store import StrategyStore


def _strategy(store: StrategyStore):
    return store.create(
        id="breakout",
        name="Breakout",
        target_instruments=["ETH-USDT-SWAP"],
        entry_signal={"type": "price_above", "symbol": "self", "value": 100},
        default_rules={"close_conditions": [{
            "purpose": "stop_loss",
            "expression": {"type": "price_below", "symbol": "self", "value": 90},
        }]},
        metadata={
            "position_side": "long",
            "order_size_contracts": {"ETH-USDT-SWAP": "1"},
            "max_entry_slippage_pct": "0.005",
        },
    )


def test_strategy_runtime_composes_entry_and_filter_but_not_exit(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = _strategy(store)
    store.create_signal_expression(
        strategy_id=strategy.id,
        purpose="filter",
        expression={"type": "rapid_rise", "symbol": "BTC-USDT-SWAP", "window_seconds": 60, "change_pct": 1},
    )
    store.create_signal_expression(
        strategy_id=strategy.id,
        purpose="exit",
        expression={"type": "price_below", "symbol": "self", "value": 90},
    )

    entry = compose_entry_expression(strategy, store)
    exits = close_condition_specs(strategy, store)

    assert entry["op"] == "and"
    assert [item["type"] for item in entry["conditions"]] == ["price_above", "rapid_rise"]
    assert exits[0]["expression"]["type"] == "price_below"
    assert validate_strategy_for_execution(strategy, store) == []


def test_strategy_runtime_resolves_self_without_mutating_persisted_expression(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = _strategy(store)

    resolved = resolve_self_symbol(strategy.entry_signal, "ETH-USDT-SWAP")

    assert resolved["symbol"] == "ETH-USDT-SWAP"
    assert strategy.entry_signal["symbol"] == "self"


def test_strategy_runtime_requires_exchange_protective_stop(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = _strategy(store)
    strategy = store.update(
        strategy.id,
        default_rules={"close_conditions": [{
            "purpose": "exit",
            "expression": {"type": "rapid_drop", "symbol": "self", "window_seconds": 60, "change_pct": 2},
        }]},
    )

    errors = validate_strategy_for_execution(strategy, store)

    assert any("absolute stop_loss" in error for error in errors)


def test_strategy_runtime_prices_fok_limit_from_persisted_slippage(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = _strategy(store)

    assert entry_limit_price(strategy, 2000) == 2010
    short = store.update(
        strategy.id,
        metadata={**strategy.metadata, "position_side": "short"},
    )
    assert entry_limit_price(short, 2000) == 1990
