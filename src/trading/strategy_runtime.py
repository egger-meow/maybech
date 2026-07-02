"""Runtime contract for executing persisted signal-based strategies."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from src.trading.signal_engine import SignalExpressionEngine
from src.trading.strategy_store import StrategyRecord, StrategyStore
from src.trading.position_rule_model import materialize_position_rule


def compose_entry_expression(
    strategy: StrategyRecord,
    store: StrategyStore,
) -> dict[str, Any]:
    expressions: list[dict[str, Any]] = []
    if strategy.entry_signal:
        expressions.append(strategy.entry_signal)
    expressions.extend(
        item.expression
        for item in store.list_signal_expressions(strategy.id)
        if item.purpose in {"entry", "filter"}
    )
    if not expressions:
        return {}
    if len(expressions) == 1:
        return deepcopy(expressions[0])
    return {"op": "and", "conditions": deepcopy(expressions)}


def resolve_self_symbol(expression: dict[str, Any], inst_id: str) -> dict[str, Any]:
    resolved = deepcopy(expression)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("symbol") == "self":
                node["symbol"] = inst_id
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(resolved)
    return resolved


def position_side(strategy: StrategyRecord) -> str:
    return str(strategy.metadata.get("position_side") or "").lower()


def candle_bar(strategy: StrategyRecord) -> str:
    return str(strategy.metadata.get("candle_bar") or "1m")


def order_size(strategy: StrategyRecord, inst_id: str) -> str | None:
    values = strategy.metadata.get("order_size_contracts")
    if not isinstance(values, dict):
        return None
    value = values.get(inst_id)
    return None if value in (None, "") else str(value)


def max_entry_slippage_pct(strategy: StrategyRecord) -> Decimal | None:
    value = strategy.metadata.get("max_entry_slippage_pct")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite() or parsed <= 0 or parsed > Decimal("0.05"):
        return None
    return parsed


def entry_limit_price(strategy: StrategyRecord, observed_price: float) -> float:
    slippage = max_entry_slippage_pct(strategy)
    if slippage is None:
        raise ValueError(
            "metadata.max_entry_slippage_pct must be greater than 0 and at most 0.05"
        )
    price = Decimal(str(observed_price))
    if not price.is_finite() or price <= 0:
        raise ValueError("observed entry price must be positive")
    multiplier = Decimal("1") + slippage
    if position_side(strategy) == "short":
        multiplier = Decimal("1") - slippage
    return float(price * multiplier)


def close_condition_specs(
    strategy: StrategyRecord,
    store: StrategyStore,
) -> list[dict[str, Any]]:
    values = strategy.default_rules.get("close_conditions")
    conditions = (
        [deepcopy(item) for item in values if isinstance(item, dict)]
        if isinstance(values, list)
        else []
    )
    conditions.extend(
        {
            "purpose": "exit",
            "expression": deepcopy(item.expression),
            "enabled": True,
            "metadata": {"source_signal_expression_id": item.id},
        }
        for item in store.list_signal_expressions(strategy.id)
        if item.purpose == "exit"
    )
    return conditions


def materialized_close_condition_specs(
    strategy: StrategyRecord,
    store: StrategyStore,
    *,
    inst_id: str,
    entry_price: object,
    basis: str,
) -> list[dict[str, Any]]:
    return [
        materialize_position_rule(
            spec,
            entry_price=entry_price,
            inst_id=inst_id,
            side=position_side(strategy),
            basis=basis,
        )
        for spec in close_condition_specs(strategy, store)
    ]


def exchange_protection_prices(
    strategy: StrategyRecord,
    store: StrategyStore,
    inst_id: str,
    entry_price: object | None = None,
    materialization_basis: str = "provisional_order_price",
) -> tuple[float, float | None]:
    side = position_side(strategy)
    stop_type = "price_below" if side == "long" else "price_above"
    take_profit_type = "price_above" if side == "long" else "price_below"
    stop_loss: float | None = None
    take_profit: float | None = None
    specs = close_condition_specs(strategy, store)
    if entry_price is not None:
        specs = materialized_close_condition_specs(
            strategy,
            store,
            inst_id=inst_id,
            entry_price=entry_price,
            basis=materialization_basis,
        )
    for spec in specs:
        if not spec.get("enabled", True):
            continue
        expression = resolve_self_symbol(spec.get("expression") or {}, inst_id)
        if expression.get("symbol") != inst_id:
            continue
        if spec.get("purpose") == "stop_loss" and expression.get("type") == stop_type:
            stop_loss = float(expression["value"])
        if spec.get("purpose") == "take_profit" and expression.get("type") == take_profit_type:
            metadata = spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {}
            template = metadata.get("template_definition")
            style = template.get("style") if isinstance(template, dict) else None
            definition = metadata.get("rule_definition")
            action = definition.get("action") if isinstance(definition, dict) else {}
            if style != "fixed_percent" and action.get("type") != "reduce_position":
                take_profit = float(expression["value"])
    if stop_loss is None:
        raise ValueError(
            f"{inst_id} requires an enabled side-consistent absolute stop_loss condition"
        )
    return stop_loss, take_profit


def validate_strategy_for_execution(
    strategy: StrategyRecord,
    store: StrategyStore,
) -> list[str]:
    engine = SignalExpressionEngine()
    errors: list[str] = []
    expression = compose_entry_expression(strategy, store)
    if not expression:
        errors.append("strategy must have an entry signal expression")
    else:
        errors.extend(
            f"entry_signal: {error}" for error in engine.validate(expression).errors
        )

    if not strategy.target_instruments:
        errors.append("strategy must target at least one instrument")
    if position_side(strategy) not in {"long", "short"}:
        errors.append("metadata.position_side must be 'long' or 'short'")
    if max_entry_slippage_pct(strategy) is None:
        errors.append(
            "metadata.max_entry_slippage_pct must be greater than 0 and at most 0.05"
        )

    for inst_id in strategy.target_instruments:
        value = order_size(strategy, inst_id)
        try:
            valid_size = value is not None and Decimal(value).is_finite() and Decimal(value) > 0
        except (InvalidOperation, ValueError):
            valid_size = False
        if not valid_size:
            errors.append(
                f"metadata.order_size_contracts.{inst_id} must be a positive contract count"
            )

    conditions = close_condition_specs(strategy, store)
    enabled_conditions = [item for item in conditions if item.get("enabled", True)]
    if not enabled_conditions:
        errors.append("default_rules.close_conditions must include an enabled condition")
    for index, condition in enumerate(conditions):
        expression = condition.get("expression")
        if not isinstance(expression, dict):
            errors.append(f"default_rules.close_conditions[{index}].expression must be an object")
            continue
        validation_expression = expression
        definition = (
            condition.get("metadata", {}).get("rule_definition", {})
            if isinstance(condition.get("metadata"), dict)
            else {}
        )
        if definition.get("style") in {"fixed_percent", "fixed_price", "evidence_target"}:
            try:
                materialized = materialize_position_rule(
                    condition,
                    entry_price=100,
                    inst_id=strategy.target_instruments[0] if strategy.target_instruments else "SELF",
                    side=position_side(strategy),
                    basis="validation_reference",
                )
                validation_expression = materialized["expression"]
            except ValueError as exc:
                errors.append(f"default_rules.close_conditions[{index}]: {exc}")
                continue
        errors.extend(
            f"default_rules.close_conditions[{index}]: {error}"
            for error in engine.validate(validation_expression).errors
        )
    if position_side(strategy) in {"long", "short"}:
        for inst_id in strategy.target_instruments:
            try:
                exchange_protection_prices(
                    strategy,
                    store,
                    inst_id,
                    entry_price=100,
                    materialization_basis="validation_reference",
                )
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(str(exc))
    return errors
