import pytest

from src.trading.logical_position_store import LogicalPositionRecord, LogicalPositionStore
from src.trading.position_rule_model import normalize_default_rules, normalize_position_rule
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
        "type": "reduce_position", "quantity_fraction": 0.25
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
