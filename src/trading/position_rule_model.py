"""Canonical persisted rule definitions shared by strategies and positions."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


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
    action = deepcopy(supplied.get("action"))
    if not isinstance(action, dict):
        action = _default_action(purpose, result)
    action_type = str(action.get("type") or "")
    if action_type not in RULE_ACTIONS:
        raise ValueError(f"unsupported position rule action: {action_type or 'missing'}")
    quantity_fraction = action.get("quantity_fraction")
    if action_type == "reduce_position":
        try:
            fraction = float(quantity_fraction)
        except (TypeError, ValueError) as exc:
            raise ValueError("reduce_position requires quantity_fraction") from exc
        if not 0 < fraction < 1:
            raise ValueError("reduce_position quantity_fraction must be between 0 and 1")
        action["quantity_fraction"] = fraction
    elif quantity_fraction is not None:
        raise ValueError("quantity_fraction is valid only for reduce_position")

    evidence = result.get("evidence", supplied.get("evidence", {}))
    if not isinstance(evidence, dict):
        raise ValueError("position rule evidence must be an object")
    parameters = result.get("parameters", supplied.get("parameters", {}))
    if not isinstance(parameters, dict):
        raise ValueError("position rule parameters must be an object")
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
        return {"type": "reduce_position", "quantity_fraction": fraction}
    return {"type": "close_position"}
