"""Canonical persisted rule definitions shared by strategies and positions."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any

from src.trading.fee_defaults import DEFAULT_ENTRY_FEE_RATE, DEFAULT_EXIT_FEE_RATE


RULE_SCHEMA_VERSION = 1
RULE_PURPOSES = {
    "stop_loss",
    "take_profit",
    "break_even",
    "trailing",
    "manual_review",
    "exit",
}
RULE_ACTIONS = {
    "close_position",
    "reduce_position",
    "amend_stop",
    "require_manual_review",
}
RULE_STYLES = {
    "absolute_price",
    "fixed_percent",
    "fixed_price",
    "evidence_target",
    "previous_swing_level",
    "signal_triggered",
    "break_even_threshold",
    "trailing_threshold",
    "manual_review",
}

# previous_swing_level parameters, validated for presence (not price — see
# resolve_dynamic_rule_templates, which resolves a concrete price shortly
# before materialize_position_rule is called).
PREVIOUS_SWING_LEVEL_PARAMETERS = {"bar", "kind", "nth", "min_score", "buffer_pct"}


def normalize_position_rule(
    *,
    purpose: str,
    expression: dict[str, Any],
    enabled: bool,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return metadata containing a validated canonical rule definition."""
    if purpose not in RULE_PURPOSES:
        raise ValueError(f"unsupported position rule purpose: {purpose}")
    if not isinstance(expression, dict) or not expression:
        raise ValueError("position rule expression must be a non-empty object")
    result = deepcopy(metadata or {})
    supplied = result.get("rule_definition")
    supplied = deepcopy(supplied) if isinstance(supplied, dict) else {}
    style = str(supplied.get("style") or _infer_style(purpose, expression))
    if style not in RULE_STYLES:
        raise ValueError(f"unsupported position rule style: {style}")
    action = deepcopy(supplied.get("action"))
    if not isinstance(action, dict):
        action = _default_action(purpose, result)
    action_type = str(action.get("type") or "")
    if action_type not in RULE_ACTIONS:
        raise ValueError(f"unsupported position rule action: {action_type or 'missing'}")
    valid_action_purposes = {
        "close_position": {"stop_loss", "take_profit", "trailing", "exit"},
        "reduce_position": {"take_profit", "trailing"},
        "amend_stop": {"break_even", "trailing"},
        "require_manual_review": {"manual_review"},
    }
    if purpose not in valid_action_purposes[action_type]:
        raise ValueError(f"{action_type} action is not valid for {purpose}")
    quantity_fraction = action.get("quantity_fraction")
    if action_type == "reduce_position":
        try:
            fraction = float(quantity_fraction)
        except (TypeError, ValueError) as exc:
            raise ValueError("reduce_position requires quantity_fraction") from exc
        if not 0 < fraction < 1:
            raise ValueError("reduce_position quantity_fraction must be between 0 and 1")
        action["quantity_fraction"] = fraction
        quantity_basis = str(action.get("quantity_basis") or "initial")
        if quantity_basis not in {"initial", "remaining"}:
            raise ValueError("reduce_position quantity_basis must be initial or remaining")
        action["quantity_basis"] = quantity_basis
    elif quantity_fraction is not None:
        raise ValueError("quantity_fraction is valid only for reduce_position")

    evidence = result.get("evidence", supplied.get("evidence", {}))
    if not isinstance(evidence, dict):
        raise ValueError("position rule evidence must be an object")
    parameters = result.get("parameters", supplied.get("parameters", {}))
    if not isinstance(parameters, dict):
        raise ValueError("position rule parameters must be an object")
    if purpose == "trailing":
        parameters = _normalize_trailing_parameters(parameters, action_type)
    if style == "previous_swing_level":
        if purpose not in {"stop_loss", "take_profit"}:
            raise ValueError("previous_swing_level is only valid for stop_loss or take_profit")
        parameters = _normalize_previous_swing_level_parameters(parameters)
    definition = {
        "schema_version": RULE_SCHEMA_VERSION,
        "purpose": purpose,
        "style": style,
        "enabled": bool(enabled),
        "trigger": deepcopy(expression),
        "action": action,
        "parameters": deepcopy(parameters),
        "evidence": deepcopy(evidence),
    }
    result["rule_definition"] = definition
    return result


def normalize_default_rules(default_rules: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(default_rules or {})
    conditions = result.get("close_conditions", [])
    if conditions is None:
        conditions = []
    if not isinstance(conditions, list):
        raise ValueError("default_rules.close_conditions must be a list")
    normalized: list[dict[str, Any]] = []
    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            raise ValueError(f"default_rules.close_conditions[{index}] must be an object")
        item = deepcopy(condition)
        purpose = str(item.get("purpose") or "exit")
        expression = item.get("expression")
        if not isinstance(expression, dict):
            raise ValueError(
                f"default_rules.close_conditions[{index}].expression must be an object"
            )
        enabled = bool(item.get("enabled", True))
        item["purpose"] = purpose
        item["enabled"] = enabled
        item["metadata"] = normalize_position_rule(
            purpose=purpose,
            expression=expression,
            enabled=enabled,
            metadata=item.get("metadata") if isinstance(item.get("metadata"), dict) else {},
        )
        normalized.append(item)
    result["close_conditions"] = normalized
    result["rule_schema_version"] = RULE_SCHEMA_VERSION
    staged_fraction = sum(
        float(item["metadata"]["rule_definition"]["action"]["quantity_fraction"])
        for item in normalized
        if item["enabled"]
        and item["metadata"]["rule_definition"]["action"].get("type") == "reduce_position"
        and item["metadata"]["rule_definition"]["action"].get("quantity_basis") == "initial"
    )
    if staged_fraction > 1 + 1e-12:
        raise ValueError("enabled staged take-profit fractions cannot exceed initial quantity")
    if staged_fraction:
        result["staged_take_profit"] = {
            "initial_quantity_fraction": staged_fraction,
            "running_remainder_fraction": max(0.0, 1.0 - staged_fraction),
        }
    else:
        result.pop("staged_take_profit", None)
    return result


def materialize_position_rule(
    rule: dict[str, Any],
    *,
    entry_price: object,
    inst_id: str,
    side: str,
    basis: str = "confirmed_entry",
) -> dict[str, Any]:
    """Resolve one canonical entry-relative rule to an absolute price trigger."""
    result = deepcopy(rule)
    metadata = result.get("metadata")
    metadata = deepcopy(metadata) if isinstance(metadata, dict) else {}
    definition = metadata.get("rule_definition")
    if not isinstance(definition, dict):
        metadata = normalize_position_rule(
            purpose=str(result.get("purpose") or "exit"),
            expression=result.get("expression") or {},
            enabled=bool(result.get("enabled", True)),
            metadata=metadata,
        )
        definition = metadata["rule_definition"]
    template = metadata.get("template_definition")
    source_definition = deepcopy(template) if isinstance(template, dict) else deepcopy(definition)
    style = str(source_definition.get("style") or "signal_triggered")
    purpose = str(source_definition.get("purpose") or result.get("purpose") or "exit")
    normalized_side = side.lower()
    if normalized_side not in {"long", "short"}:
        raise ValueError("position rule materialization requires long or short side")
    try:
        entry = Decimal(str(entry_price))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("position rule materialization entry price is invalid") from exc
    if not entry.is_finite() or entry <= 0:
        raise ValueError("position rule materialization entry price must be positive")

    trigger = deepcopy(source_definition.get("trigger") or result.get("expression") or {})
    parameters = source_definition.get("parameters")
    parameters = parameters if isinstance(parameters, dict) else {}
    if style == "fixed_percent":
        try:
            offset = Decimal(str(parameters.get("offset_pct")))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("fixed_percent rule requires offset_pct") from exc
        if not offset.is_finite() or offset <= 0 or offset >= 1:
            raise ValueError("fixed_percent offset_pct must be between 0 and 1")
        favorable = purpose == "take_profit"
        increase = (normalized_side == "long") == favorable
        price = entry * (Decimal("1") + offset if increase else Decimal("1") - offset)
        trigger = _price_trigger(purpose, normalized_side, inst_id, price)
    elif style == "break_even_threshold":
        activation_price = parameters.get("activation_price")
        activation_pct = parameters.get("activation_profit_pct")
        if activation_price is not None:
            try:
                threshold = Decimal(str(activation_price))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("break-even activation_price is invalid") from exc
        else:
            try:
                threshold_pct = Decimal(str(activation_pct))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(
                    "break-even rule requires activation_profit_pct or activation_price"
                ) from exc
            if not threshold_pct.is_finite() or threshold_pct <= 0 or threshold_pct > Decimal("1"):
                raise ValueError("break-even activation_profit_pct must be between 0 and 1")
            threshold = entry * (
                Decimal("1") + threshold_pct
                if normalized_side == "long"
                else Decimal("1") - threshold_pct
            )
        if (
            (normalized_side == "long" and threshold <= entry)
            or (normalized_side == "short" and threshold >= entry)
        ):
            raise ValueError("break-even activation must be favorable to entry")
        trigger = {
            "type": "price_above" if normalized_side == "long" else "price_below",
            "symbol": inst_id,
            "value": float(threshold),
        }
    elif style == "trailing_threshold":
        activation_price = parameters.get("activation_price")
        if activation_price is None:
            activation_pct = Decimal(str(parameters["activation_profit_pct"]))
            activation_price = entry * (
                Decimal("1") + activation_pct
                if normalized_side == "long"
                else Decimal("1") - activation_pct
            )
        threshold = Decimal(str(activation_price))
        if (
            (normalized_side == "long" and threshold <= entry)
            or (normalized_side == "short" and threshold >= entry)
        ):
            raise ValueError("trailing activation must be favorable to entry")
        trigger = {
            "type": "price_above" if normalized_side == "long" else "price_below",
            "symbol": inst_id,
            "value": float(threshold),
        }
    elif style == "break_even_threshold":
        activation_price = parameters.get("activation_price")
        activation_pct = parameters.get("activation_profit_pct")
        if activation_price is not None:
            try:
                threshold = Decimal(str(activation_price))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("break-even activation_price is invalid") from exc
        else:
            try:
                threshold_pct = Decimal(str(activation_pct))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(
                    "break-even rule requires activation_profit_pct or activation_price"
                ) from exc
            if not threshold_pct.is_finite() or threshold_pct <= 0 or threshold_pct > Decimal("1"):
                raise ValueError("break-even activation_profit_pct must be between 0 and 1")
            threshold = entry * (
                Decimal("1") + threshold_pct
                if normalized_side == "long"
                else Decimal("1") - threshold_pct
            )
        if (
            (normalized_side == "long" and threshold <= entry)
            or (normalized_side == "short" and threshold >= entry)
        ):
            raise ValueError("break-even activation must be favorable to entry")
        trigger = {
            "type": "price_above" if normalized_side == "long" else "price_below",
            "symbol": inst_id,
            "value": float(threshold),
        }
    elif style in {"fixed_price", "evidence_target"}:
        value = parameters.get("target_price", parameters.get("price"))
        if value is None and isinstance(trigger, dict):
            value = trigger.get("value")
        try:
            price = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{style} rule requires a valid target price") from exc
        if not price.is_finite() or price <= 0:
            raise ValueError(f"{style} target price must be positive")
        if style == "evidence_target" and not source_definition.get("evidence"):
            raise ValueError("evidence_target rule requires structured evidence")
        if basis != "validation_reference":
            _validate_price_side(purpose, normalized_side, entry, price)
        trigger = _price_trigger(purpose, normalized_side, inst_id, price)
    elif style == "absolute_price":
        trigger = deepcopy(trigger)
        if trigger.get("symbol") == "self":
            trigger["symbol"] = inst_id
        if purpose in {"stop_loss", "take_profit"}:
            try:
                absolute_price = Decimal(str(trigger.get("value")))
            except (InvalidOperation, ValueError) as exc:
                raise ValueError("absolute price rule requires a valid value") from exc
            if basis != "validation_reference":
                _validate_price_side(purpose, normalized_side, entry, absolute_price)
    elif style == "previous_swing_level":
        raise ValueError(
            "previous_swing_level rules must be resolved to a concrete price "
            "before materialization (see resolve_dynamic_rule_templates)"
        )

    materialized_definition = {
        **source_definition,
        "trigger": trigger,
        "parameters": deepcopy(parameters),
    }
    metadata["template_definition"] = source_definition
    metadata["rule_definition"] = materialized_definition
    metadata["materialization"] = {
        "status": "materialized",
        "basis": basis,
        "entry_price": str(entry),
        "inst_id": inst_id,
        "side": normalized_side,
    }
    result["purpose"] = purpose
    result["expression"] = trigger
    result["enabled"] = bool(source_definition.get("enabled", result.get("enabled", True)))
    result["metadata"] = metadata
    return result


def _price_trigger(purpose: str, side: str, inst_id: str, price: Decimal) -> dict[str, Any]:
    if purpose not in {"stop_loss", "take_profit"}:
        raise ValueError(f"{purpose} cannot use a price materialization style")
    above = (purpose == "take_profit" and side == "long") or (
        purpose == "stop_loss" and side == "short"
    )
    return {
        "type": "price_above" if above else "price_below",
        "symbol": inst_id,
        "value": float(price),
    }


def _validate_price_side(
    purpose: str,
    side: str,
    entry: Decimal,
    price: Decimal,
) -> None:
    favorable = purpose == "take_profit"
    must_be_above = (side == "long") == favorable
    if (must_be_above and price <= entry) or (not must_be_above and price >= entry):
        relation = "above" if must_be_above else "below"
        raise ValueError(f"{purpose} price must be {relation} entry for {side}")


def calculate_break_even_target(
    *,
    entry_price: object,
    side: str,
    entry_fee_rate: object = DEFAULT_ENTRY_FEE_RATE,
    exit_fee_rate: object = DEFAULT_EXIT_FEE_RATE,
    slippage_rate: object = "0.0005",
    lock_in_pct: object = "0",
) -> tuple[Decimal, dict[str, str]]:
    """Return a target whose modeled net return is non-negative after costs."""
    try:
        entry = Decimal(str(entry_price))
        entry_fee = Decimal(str(entry_fee_rate))
        exit_fee = Decimal(str(exit_fee_rate))
        slippage = Decimal(str(slippage_rate))
        lock_in = Decimal(str(lock_in_pct))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("break-even inputs must be finite decimals") from exc
    values = (entry, entry_fee, exit_fee, slippage, lock_in)
    if any(not value.is_finite() for value in values) or entry <= 0:
        raise ValueError("break-even inputs must be finite and entry must be positive")
    if any(value < 0 or value > Decimal("0.02") for value in (entry_fee, exit_fee, slippage)):
        raise ValueError("break-even fee and slippage rates must be between 0 and 0.02")
    if lock_in < 0 or lock_in > Decimal("0.05"):
        raise ValueError("break-even lock_in_pct must be between 0 and 0.05")
    normalized_side = side.lower()
    entry_cost = entry_fee + slippage
    exit_cost = exit_fee + slippage
    if normalized_side == "long":
        if exit_cost >= 1:
            raise ValueError("break-even exit costs are invalid")
        target = entry * (Decimal("1") + entry_cost) / (Decimal("1") - exit_cost)
        target *= Decimal("1") + lock_in
        net_return = (
            target * (Decimal("1") - exit_cost)
            - entry * (Decimal("1") + entry_cost)
        ) / (entry * (Decimal("1") + entry_cost))
    elif normalized_side == "short":
        target = entry * (Decimal("1") - entry_cost) / (Decimal("1") + exit_cost)
        target *= Decimal("1") - lock_in
        net_return = (
            entry * (Decimal("1") - entry_cost)
            - target * (Decimal("1") + exit_cost)
        ) / (entry * (Decimal("1") - entry_cost))
    else:
        raise ValueError("break-even side must be long or short")
    return target, {
        "entry_fee_rate": str(entry_fee),
        "exit_fee_rate": str(exit_fee),
        "slippage_rate": str(slippage),
        "lock_in_pct": str(lock_in),
        "modeled_net_return_pct": str(net_return),
    }


def _normalize_trailing_parameters(
    parameters: dict[str, Any],
    action_type: str,
) -> dict[str, Any]:
    result = deepcopy(parameters)
    kind = str(result.get("trailing_kind") or "")
    if kind not in {"stop", "take_profit"}:
        raise ValueError("trailing_kind must be stop or take_profit")
    if kind == "stop" and action_type != "amend_stop":
        raise ValueError("trailing stop requires amend_stop")
    if kind == "take_profit" and action_type not in {"close_position", "reduce_position"}:
        raise ValueError("trailing take-profit requires close_position or reduce_position")
    activation_price = result.get("activation_price")
    activation_pct = result.get("activation_profit_pct")
    if (activation_price is None) == (activation_pct is None):
        raise ValueError("trailing requires exactly one activation_price or activation_profit_pct")
    distance = result.get("distance")
    distance_pct = result.get("distance_pct")
    if (distance is None) == (distance_pct is None):
        raise ValueError("trailing requires exactly one distance or distance_pct")
    for key, value, upper in (
        ("activation_price", activation_price, None),
        ("activation_profit_pct", activation_pct, Decimal("1")),
        ("distance", distance, None),
        ("distance_pct", distance_pct, Decimal("0.5")),
    ):
        if value is None:
            continue
        try:
            parsed = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"trailing {key} is invalid") from exc
        if not parsed.is_finite() or parsed <= 0 or (upper is not None and parsed > upper):
            raise ValueError(f"trailing {key} is outside its allowed range")
        result[key] = str(parsed)
    timeframe = str(result.get("timeframe") or "")
    if not timeframe:
        raise ValueError("trailing timeframe is required")
    try:
        stale_after = int(result.get("stale_after_seconds", 90))
    except (TypeError, ValueError) as exc:
        raise ValueError("trailing stale_after_seconds must be an integer") from exc
    if stale_after < 5 or stale_after > 3600:
        raise ValueError("trailing stale_after_seconds must be between 5 and 3600")
    result["timeframe"] = timeframe
    result["stale_after_seconds"] = stale_after
    return result


def _normalize_previous_swing_level_parameters(parameters: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(parameters)
    missing = PREVIOUS_SWING_LEVEL_PARAMETERS - result.keys()
    if missing:
        raise ValueError(
            f"previous_swing_level requires parameters: {', '.join(sorted(missing))}"
        )
    bar = str(result.get("bar") or "")
    if not bar:
        raise ValueError("previous_swing_level bar is required")
    kind = str(result.get("kind") or "")
    if kind not in {"support", "resistance"}:
        raise ValueError("previous_swing_level kind must be support or resistance")
    try:
        nth = int(result.get("nth"))
    except (TypeError, ValueError) as exc:
        raise ValueError("previous_swing_level nth must be an integer") from exc
    if nth < 1:
        raise ValueError("previous_swing_level nth must be at least 1")
    try:
        min_score = Decimal(str(result.get("min_score")))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("previous_swing_level min_score is invalid") from exc
    if not min_score.is_finite() or min_score < 0 or min_score > 1:
        raise ValueError("previous_swing_level min_score must be between 0 and 1")
    try:
        buffer_pct = Decimal(str(result.get("buffer_pct")))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("previous_swing_level buffer_pct is invalid") from exc
    if not buffer_pct.is_finite() or buffer_pct < 0 or buffer_pct > Decimal("0.5"):
        raise ValueError("previous_swing_level buffer_pct must be between 0 and 0.5")
    result["bar"] = bar
    result["kind"] = kind
    result["nth"] = nth
    result["min_score"] = str(min_score)
    result["buffer_pct"] = str(buffer_pct)
    return result


def _infer_style(purpose: str, expression: dict[str, Any]) -> str:
    if purpose in {"stop_loss", "take_profit"} and expression.get("type") in {
        "price_above", "price_below"
    }:
        return "absolute_price"
    return {
        "break_even": "break_even_threshold",
        "trailing": "trailing_threshold",
        "manual_review": "manual_review",
    }.get(purpose, "signal_triggered")


def _default_action(purpose: str, metadata: dict[str, Any]) -> dict[str, Any]:
    if purpose in {"break_even", "trailing"}:
        return {"type": "amend_stop"}
    if purpose == "manual_review":
        return {"type": "require_manual_review"}
    fraction = metadata.get("quantity_fraction")
    if purpose == "take_profit" and fraction is not None:
        return {
            "type": "reduce_position",
            "quantity_fraction": fraction,
            "quantity_basis": "initial",
        }
    return {"type": "close_position"}
