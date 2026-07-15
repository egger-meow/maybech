"""Runtime contract for executing persisted signal-based strategies."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from src.market.impulse_origin import find_impulse_origin
from src.market.support_resistance import find_swing_level
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


def resolve_dynamic_rule_templates(
    conditions: list[dict[str, Any]],
    *,
    client: object | None,
    inst_id: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Rewrite any dynamically-resolved template (`previous_swing_level`,
    `impulse_origin`) to a concrete `fixed_price` template before
    `materialize_position_rule` runs (which stays pure — no candle I/O).
    This is the only place such a rule's price is resolved; every later
    materialization (confirmed-fill re-basis, etc.) sees the already-concrete
    `fixed_price` template this rewrote it to.

    With a live `client`, this resolves a fresh level from current candles —
    call this only once, at the point a new position's close conditions are
    prepared, so each entry gets an independently fresh level without ever
    mutating the strategy's reusable default_rules template. Without a
    client (save-time validation / dry preview with no exchange connection),
    it falls back to whatever placeholder price is already on the raw
    trigger — the same degraded-but-non-blocking behavior every other style
    already gets when materialized with a dummy entry_price.
    """
    resolved: list[dict[str, Any]] = []
    for condition in conditions:
        item = deepcopy(condition)
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        definition = metadata.get("rule_definition") if isinstance(metadata.get("rule_definition"), dict) else {}
        style = definition.get("style")
        if style not in {"previous_swing_level", "impulse_origin"}:
            resolved.append(item)
            continue
        parameters = definition.get("parameters") or {}
        if client is not None:
            if style == "previous_swing_level":
                resolution = find_swing_level(
                    client,
                    inst_id,
                    bar=str(parameters.get("bar")),
                    kind=str(parameters.get("kind")),
                    nth=int(parameters.get("nth", 1)),
                    min_score=float(parameters.get("min_score", 0)),
                    buffer_pct=float(parameters.get("buffer_pct", 0)),
                    now=now,
                )
            else:
                resolution = find_impulse_origin(
                    client,
                    inst_id,
                    bar=str(parameters.get("bar")),
                    kind=str(parameters.get("kind")),
                    nth=int(parameters.get("nth", 1)),
                    min_volume_multiple=float(parameters.get("min_volume_multiple", 2.0)),
                    min_body_ratio=float(parameters.get("min_body_ratio", 0.6)),
                    min_body_vs_baseline_multiple=float(
                        parameters.get("min_body_vs_baseline_multiple", 1.5)
                    ),
                    buffer_pct=float(parameters.get("buffer_pct", 0)),
                )
            target_price = resolution["price"]
            evidence = resolution["evidence"]
        else:
            trigger = definition.get("trigger") or item.get("expression") or {}
            target_price = trigger.get("value")
            evidence = definition.get("evidence") or {}
        metadata["rule_definition"] = {
            **definition,
            "style": "fixed_price",
            "parameters": {**parameters, "target_price": target_price},
            "evidence": evidence,
        }
        item["metadata"] = metadata
        resolved.append(item)
    return resolved


def materialized_close_condition_specs(
    strategy: StrategyRecord,
    store: StrategyStore,
    *,
    inst_id: str,
    entry_price: object,
    basis: str,
    client: object | None = None,
) -> list[dict[str, Any]]:
    specs = resolve_dynamic_rule_templates(
        close_condition_specs(strategy, store), client=client, inst_id=inst_id,
    )
    return [
        materialize_position_rule(
            spec,
            entry_price=entry_price,
            inst_id=inst_id,
            side=position_side(strategy),
            basis=basis,
        )
        for spec in specs
    ]


def exchange_protection_prices(
    strategy: StrategyRecord,
    store: StrategyStore,
    inst_id: str,
    entry_price: object | None = None,
    materialization_basis: str = "provisional_order_price",
    client: object | None = None,
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
            client=client,
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
    entry_expression = expression
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
    uses_impulse_origin = any(
        (
            condition.get("metadata", {}).get("rule_definition", {})
            if isinstance(condition.get("metadata"), dict)
            else {}
        ).get("style") == "impulse_origin"
        for condition in enabled_conditions
    )
    if uses_impulse_origin and not _expression_contains_signal_type(entry_expression, "volume_multiple"):
        errors.append(
            "default_rules.close_conditions: impulse_origin (起漲點) stop style requires the "
            "strategy's entry signal to include a volume_multiple (volume burst) condition"
        )
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
        if definition.get("style") in {
            "fixed_percent", "fixed_price", "evidence_target", "break_even_threshold"
        }:
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


def _expression_contains_signal_type(expression: Any, signal_type: str) -> bool:
    if not isinstance(expression, dict):
        return False
    if "op" in expression:
        return any(
            _expression_contains_signal_type(condition, signal_type)
            for condition in expression.get("conditions") or []
        )
    return expression.get("type") == signal_type
