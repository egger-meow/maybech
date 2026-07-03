import pytest

from src.trading.logical_position_store import LogicalPositionRecord, LogicalPositionStore
from src.trading.position_rule_model import calculate_break_even_target, materialize_position_rule, normalize_default_rules, normalize_position_rule
from src.trading.strategy_store import StrategyStore


def test_canonical_rule_definition_is_shared_by_strategy_and_position(tmp_path):
    expression = {"type": "price_below", "symbol": "self", "value": 2900}
    defaults = normalize_default_rules({
        "close_conditions": [{
            "purpose": "stop_loss",
            "expression": expression,
            "enabled": True,
            "metadata": {"evidence": {"source": "operator_chart_anchor"}},
        }]
    })
    definition = defaults["close_conditions"][0]["metadata"]["rule_definition"]

    assert defaults["rule_schema_version"] == 1
    assert definition["purpose"] == "stop_loss"
    assert definition["style"] == "absolute_price"
    assert definition["action"] == {"type": "close_position"}
    assert definition["trigger"] == expression
    assert definition["evidence"]["source"] == "operator_chart_anchor"

    position_store = LogicalPositionStore(str(tmp_path / "rules.db"))
    position_store.save(LogicalPositionRecord(
        id="unit", source="manual", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=1, remaining_quantity=1, entry_price=3000, status="open",
    ))
    condition = position_store.create_close_condition(
        position_id="unit", purpose="stop_loss", expression=expression,
        metadata={"evidence": {"source": "operator_chart_anchor"}},
    )

    assert condition is not None
    assert condition.metadata["rule_definition"] == definition


def test_take_profit_partial_action_requires_safe_fraction():
    metadata = normalize_position_rule(
        purpose="take_profit",
        expression={"type": "price_above", "symbol": "self", "value": 3200},
        enabled=True,
        metadata={"quantity_fraction": 0.25},
    )
    assert metadata["rule_definition"]["action"] == {
        "type": "reduce_position", "quantity_fraction": 0.25,
        "quantity_basis": "initial",
    }

    with pytest.raises(ValueError, match="between 0 and 1"):
        normalize_position_rule(
            purpose="take_profit",
            expression={"type": "price_above", "symbol": "self", "value": 3200},
            enabled=True,
            metadata={"quantity_fraction": 1.2},
        )


def test_strategy_store_persists_canonical_default_rule(tmp_path):
    store = StrategyStore(str(tmp_path / "rules.db"))
    strategy = store.create(
        id="strategy", name="Strategy", target_instruments=["ETH-USDT-SWAP"],
        default_rules={"close_conditions": [{
            "purpose": "stop_loss", "enabled": True,
            "expression": {"type": "price_below", "symbol": "self", "value": 2900},
        }]},
    )

    saved = store.get(strategy.id)
    assert saved is not None
    assert saved.default_rules["rule_schema_version"] == 1
    assert saved.default_rules["close_conditions"][0]["metadata"]["rule_definition"]["purpose"] == "stop_loss"


def test_store_migrations_backfill_existing_rule_definitions(tmp_path):
    db_path = str(tmp_path / "rules.db")
    strategy_store = StrategyStore(db_path)
    strategy_store.create(id="legacy", name="Legacy")
    position_store = LogicalPositionStore(db_path)
    position_store.save(LogicalPositionRecord(
        id="unit", source="manual", inst_id="ETH-USDT-SWAP", side="long",
        opened_quantity=1, remaining_quantity=1, entry_price=3000, status="open",
    ))
    condition = position_store.create_close_condition(
        position_id="unit", purpose="stop_loss",
        expression={"type": "price_below", "symbol": "self", "value": 2900},
    )
    assert condition is not None
    legacy_defaults = '{"close_conditions":[{"purpose":"stop_loss","enabled":true,"expression":{"type":"price_below","symbol":"self","value":2900}}]}'
    with strategy_store._conn() as connection:
        connection.execute(
            "UPDATE strategies SET default_rules_json = ? WHERE id = 'legacy'",
            (legacy_defaults,),
        )
        connection.execute(
            "DELETE FROM schema_migrations WHERE component = 'strategies' AND version = 5"
        )
    with position_store._conn() as connection:
        connection.execute(
            "UPDATE logical_position_close_conditions SET metadata_json = '{}' WHERE id = ?",
            (condition.id,),
        )
        connection.execute(
            "DELETE FROM schema_migrations WHERE component = 'logical_positions' AND version = 7"
        )

    migrated_strategy = StrategyStore(db_path).get("legacy")
    migrated_condition = LogicalPositionStore(db_path).get_close_condition("unit", condition.id)

    assert migrated_strategy.default_rules["rule_schema_version"] == 1
    assert migrated_strategy.default_rules["close_conditions"][0]["metadata"]["rule_definition"]["purpose"] == "stop_loss"
    assert migrated_condition.metadata["rule_definition"]["purpose"] == "stop_loss"


def test_entry_relative_and_evidence_rules_materialize_from_entry():
    stop = normalize_default_rules({"close_conditions": [{
        "purpose": "stop_loss", "enabled": True,
        "expression": {"type": "entry_relative", "symbol": "self"},
        "metadata": {"rule_definition": {
            "style": "fixed_percent", "action": {"type": "close_position"},
            "parameters": {"offset_pct": "0.05"}, "evidence": {},
        }},
    }]})["close_conditions"][0]
    target = normalize_default_rules({"close_conditions": [{
        "purpose": "take_profit", "enabled": True,
        "expression": {"type": "entry_relative", "symbol": "self"},
        "metadata": {"rule_definition": {
            "style": "evidence_target",
            "action": {"type": "reduce_position", "quantity_fraction": 0.25},
            "parameters": {"target_price": 112},
            "evidence": {"source": "support_resistance", "level_score": 0.8},
        }},
    }]})["close_conditions"][0]

    materialized_stop = materialize_position_rule(
        stop, entry_price=100, inst_id="ETH-USDT-SWAP", side="long"
    )
    materialized_target = materialize_position_rule(
        target, entry_price=100, inst_id="ETH-USDT-SWAP", side="long"
    )

    assert materialized_stop["expression"] == {
        "type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 95.0
    }
    assert materialized_target["expression"]["value"] == 112.0
    assert materialized_target["metadata"]["rule_definition"]["action"]["quantity_fraction"] == 0.25
    assert materialized_target["metadata"]["materialization"]["basis"] == "confirmed_entry"


def test_staged_targets_bound_total_initial_quantity_and_expose_remainder():
    def stage(fraction):
        return {
            "purpose": "take_profit", "enabled": True,
            "expression": {"type": "price_above", "symbol": "self", "value": 110},
            "metadata": {"quantity_fraction": fraction},
        }

    rules = normalize_default_rules({"close_conditions": [stage(0.25), stage(0.5)]})

    assert rules["staged_take_profit"] == {
        "initial_quantity_fraction": 0.75,
        "running_remainder_fraction": 0.25,
    }
    with pytest.raises(ValueError, match="cannot exceed"):
        normalize_default_rules({"close_conditions": [stage(0.6), stage(0.5)]})


def test_break_even_target_models_fees_slippage_and_lock_in_for_both_sides():
    long_target, long_evidence = calculate_break_even_target(
        entry_price=100, side="long", entry_fee_rate=0.001,
        exit_fee_rate=0.001, slippage_rate=0.001, lock_in_pct=0.01,
    )
    short_target, short_evidence = calculate_break_even_target(
        entry_price=100, side="short", entry_fee_rate=0.001,
        exit_fee_rate=0.001, slippage_rate=0.001, lock_in_pct=0.01,
    )

    assert long_target > 101
    assert short_target < 99
    assert float(long_evidence["modeled_net_return_pct"]) >= 0.01
    assert float(short_evidence["modeled_net_return_pct"]) >= 0.01


def test_break_even_rule_materializes_activation_from_entry():
    template = normalize_default_rules({"close_conditions": [{
        "purpose": "break_even", "enabled": True,
        "expression": {"type": "entry_relative", "symbol": "self"},
        "metadata": {"rule_definition": {
            "style": "break_even_threshold", "action": {"type": "amend_stop"},
            "parameters": {"activation_profit_pct": 0.03}, "evidence": {},
        }},
    }]})["close_conditions"][0]

    long = materialize_position_rule(
        template, entry_price=100, inst_id="ETH-USDT-SWAP", side="long"
    )
    short = materialize_position_rule(
        template, entry_price=100, inst_id="ETH-USDT-SWAP", side="short"
    )

    assert long["expression"]["type"] == "price_above"
    assert long["expression"]["value"] == 103
    assert short["expression"]["type"] == "price_below"
    assert short["expression"]["value"] == 97


def test_trailing_rule_materializes_activation_and_separates_stop_from_take_profit():
    stop_template = normalize_default_rules({"close_conditions": [{
        "purpose": "trailing", "enabled": True,
        "expression": {"type": "entry_relative", "symbol": "self"},
        "metadata": {"rule_definition": {
            "style": "trailing_threshold", "action": {"type": "amend_stop"},
            "parameters": {"trailing_kind": "stop", "activation_profit_pct": 0.03, "distance_pct": 0.02, "timeframe": "1m"}, "evidence": {},
        }},
    }]})["close_conditions"][0]
    take_profit_template = normalize_default_rules({"close_conditions": [{
        "purpose": "trailing", "enabled": True,
        "expression": {"type": "entry_relative", "symbol": "self"},
        "metadata": {"rule_definition": {
            "style": "trailing_threshold", "action": {"type": "reduce_position", "quantity_fraction": 0.5, "quantity_basis": "remaining"},
            "parameters": {"trailing_kind": "take_profit", "activation_profit_pct": 0.04, "distance": 2, "timeframe": "5m"}, "evidence": {},
        }},
    }]})["close_conditions"][0]

    stop = materialize_position_rule(stop_template, entry_price=100, inst_id="ETH-USDT-SWAP", side="long")
    take_profit = materialize_position_rule(take_profit_template, entry_price=100, inst_id="ETH-USDT-SWAP", side="short")

    assert stop["expression"] == {"type": "price_above", "symbol": "ETH-USDT-SWAP", "value": 103.0}
    assert take_profit["expression"] == {"type": "price_below", "symbol": "ETH-USDT-SWAP", "value": 96.0}
    with pytest.raises(ValueError, match="requires amend_stop"):
        normalize_position_rule(
            purpose="trailing", enabled=True,
            expression={"type": "price_above", "symbol": "self", "value": 103},
            metadata={"rule_definition": {"style": "trailing_threshold", "action": {"type": "close_position"}, "parameters": {"trailing_kind": "stop", "activation_profit_pct": .03, "distance_pct": .02, "timeframe": "1m"}}},
        )
