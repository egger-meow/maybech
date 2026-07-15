from datetime import datetime, timedelta, timezone

import pandas as pd

from src.trading.strategy_runtime import (
    close_condition_specs,
    compose_entry_expression,
    entry_limit_price,
    exchange_protection_prices,
    resolve_dynamic_rule_templates,
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


def test_strategy_runtime_materializes_relative_stop_but_keeps_staged_target_software_managed(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = _strategy(store)
    strategy = store.update(strategy.id, default_rules={"close_conditions": [
        {
            "purpose": "stop_loss", "enabled": True,
            "expression": {"type": "entry_relative", "symbol": "self"},
            "metadata": {"rule_definition": {
                "style": "fixed_percent", "action": {"type": "close_position"},
                "parameters": {"offset_pct": 0.05}, "evidence": {},
            }},
        },
        {
            "purpose": "take_profit", "enabled": True,
            "expression": {"type": "entry_relative", "symbol": "self"},
            "metadata": {"rule_definition": {
                "style": "fixed_percent",
                "action": {"type": "reduce_position", "quantity_fraction": 0.25},
                "parameters": {"offset_pct": 0.10}, "evidence": {},
            }},
        },
    ]})

    stop, attached_target = exchange_protection_prices(
        strategy, store, "ETH-USDT-SWAP", entry_price=100
    )

    assert stop == 95
    assert attached_target is None


def _swing_client():
    now = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)
    rows = []
    for index in range(100):
        close = 100 + (index % 10 if (index // 10) % 2 == 0 else 10 - index % 10)
        rows.append({
            "timestamp": now - timedelta(minutes=100 - index),
            "open": close - .25, "high": close + 1, "low": close - 1, "close": close,
            "volume": 10 + index % 7,
        })
    frame = pd.DataFrame(rows)

    class Client:
        def get_candles(self, *, inst_id, bar, limit):
            del inst_id, bar
            tail = frame.tail(int(limit))
            return [
                [
                    str(int(row.timestamp.timestamp() * 1000)),
                    str(row.open), str(row.high), str(row.low), str(row.close),
                    str(row.volume), str(row.volume), str(row.volume), "1",
                ]
                for row in tail.itertuples()
            ]
    return Client()


def _previous_swing_level_condition():
    return {
        "purpose": "stop_loss", "enabled": True,
        "expression": {"type": "price_below", "symbol": "self", "value": 42},
        "metadata": {"rule_definition": {
            "style": "previous_swing_level", "action": {"type": "close_position"},
            "parameters": {"bar": "1H", "kind": "support", "nth": 1, "min_score": 0.0, "buffer_pct": 0.0},
            "evidence": {},
        }},
    }


def test_resolve_dynamic_rule_templates_falls_back_to_placeholder_without_client():
    resolved = resolve_dynamic_rule_templates(
        [_previous_swing_level_condition()], client=None, inst_id="ETH-USDT-SWAP",
    )

    definition = resolved[0]["metadata"]["rule_definition"]
    assert definition["style"] == "fixed_price"
    assert definition["parameters"]["target_price"] == 42


def test_resolve_dynamic_rule_templates_resolves_a_fresh_price_with_client():
    resolved = resolve_dynamic_rule_templates(
        [_previous_swing_level_condition()],
        client=_swing_client(),
        inst_id="ETH-USDT-SWAP",
        now=datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
    )

    definition = resolved[0]["metadata"]["rule_definition"]
    assert definition["style"] == "fixed_price"
    assert definition["parameters"]["target_price"] != 42
    assert definition["evidence"]["source"] == "previous_swing_level"


def test_exchange_protection_prices_falls_back_to_placeholder_without_client(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = _strategy(store)
    strategy = store.update(
        strategy.id,
        default_rules={"close_conditions": [_previous_swing_level_condition()]},
    )

    stop, _ = exchange_protection_prices(strategy, store, "ETH-USDT-SWAP", entry_price=100)

    assert stop == 42


def test_exchange_protection_prices_resolves_previous_swing_level_with_client(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = _strategy(store)
    strategy = store.update(
        strategy.id,
        default_rules={"close_conditions": [_previous_swing_level_condition()]},
    )

    stop, _ = exchange_protection_prices(
        strategy, store, "ETH-USDT-SWAP", entry_price=200, client=_swing_client(),
    )

    assert stop != 42
    assert 0 < stop < 200
    assert validate_strategy_for_execution(strategy, store) == []


def _impulse_client():
    now = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)
    rows = []
    price = 100.0
    for index in range(80):
        if index == 60:
            open_price = price
            close = price * 1.05
            volume = 50.0
        else:
            open_price = price
            close = price * (1.001 if index % 2 == 0 else 0.999)
            volume = 10.0
        rows.append({
            "timestamp": now - timedelta(minutes=80 - index),
            "open": open_price, "high": max(open_price, close) + 0.1,
            "low": min(open_price, close) - 0.1, "close": close, "volume": volume,
        })
        price = close
    frame = pd.DataFrame(rows)

    class Client:
        def get_candles(self, *, inst_id, bar, limit):
            del inst_id, bar
            tail = frame.tail(int(limit))
            return [
                [
                    str(int(row.timestamp.timestamp() * 1000)),
                    str(row.open), str(row.high), str(row.low), str(row.close),
                    str(row.volume), str(row.volume), str(row.volume), "1",
                ]
                for row in tail.itertuples()
            ]
    return Client()


def _impulse_origin_condition():
    return {
        "purpose": "stop_loss", "enabled": True,
        "expression": {"type": "price_below", "symbol": "self", "value": 42},
        "metadata": {"rule_definition": {
            "style": "impulse_origin", "action": {"type": "close_position"},
            "parameters": {
                "bar": "1m", "kind": "bullish", "nth": 1,
                "min_volume_multiple": 2.0, "min_body_ratio": 0.6,
                "min_body_vs_baseline_multiple": 1.5, "buffer_pct": 0.0,
            },
            "evidence": {},
        }},
    }


def test_resolve_dynamic_rule_templates_falls_back_to_placeholder_for_impulse_origin_without_client():
    resolved = resolve_dynamic_rule_templates(
        [_impulse_origin_condition()], client=None, inst_id="ETH-USDT-SWAP",
    )

    definition = resolved[0]["metadata"]["rule_definition"]
    assert definition["style"] == "fixed_price"
    assert definition["parameters"]["target_price"] == 42


def test_resolve_dynamic_rule_templates_resolves_a_fresh_impulse_origin_price_with_client():
    resolved = resolve_dynamic_rule_templates(
        [_impulse_origin_condition()], client=_impulse_client(), inst_id="ETH-USDT-SWAP",
    )

    definition = resolved[0]["metadata"]["rule_definition"]
    assert definition["style"] == "fixed_price"
    assert definition["parameters"]["target_price"] != 42
    assert definition["evidence"]["source"] == "impulse_origin"


def test_exchange_protection_prices_resolves_impulse_origin_with_client(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = _strategy(store)
    strategy = store.update(
        strategy.id,
        entry_signal={
            "op": "and",
            "conditions": [
                {"type": "price_above", "symbol": "self", "value": 100},
                {"type": "volume_multiple", "symbol": "self", "timeframe": "1m", "multiplier": 3},
            ],
        },
        default_rules={"close_conditions": [_impulse_origin_condition()]},
    )

    stop, _ = exchange_protection_prices(
        strategy, store, "ETH-USDT-SWAP", entry_price=200, client=_impulse_client(),
    )

    assert stop != 42
    assert 0 < stop < 200
    assert validate_strategy_for_execution(strategy, store) == []


def test_validate_strategy_for_execution_blocks_impulse_origin_without_volume_burst_entry_signal(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = _strategy(store)
    strategy = store.update(
        strategy.id,
        default_rules={"close_conditions": [_impulse_origin_condition()]},
    )

    errors = validate_strategy_for_execution(strategy, store)

    assert any("volume_multiple" in error for error in errors)


def test_validate_strategy_for_execution_allows_impulse_origin_with_volume_burst_entry_signal(tmp_path):
    store = StrategyStore(str(tmp_path / "strategies.db"))
    strategy = _strategy(store)
    strategy = store.update(
        strategy.id,
        entry_signal={
            "op": "and",
            "conditions": [
                {"type": "price_above", "symbol": "self", "value": 100},
                {"type": "volume_multiple", "symbol": "self", "timeframe": "1m", "multiplier": 3},
            ],
        },
        default_rules={"close_conditions": [_impulse_origin_condition()]},
    )

    errors = validate_strategy_for_execution(strategy, store)

    assert not any("volume_multiple" in error for error in errors)
