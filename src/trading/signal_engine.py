"""Validation and evaluation for persisted signal expressions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


PrimitiveSignalType = Literal[
    "price_above",
    "price_below",
    "rapid_drop",
    "rapid_rise",
    "volume_multiple",
]


SIGNAL_TEMPLATES: list[dict[str, Any]] = [
    {
        "type": "price_above",
        "description": "Matches when symbol price is greater than or equal to value.",
        "required": ["symbol", "value"],
    },
    {
        "type": "price_below",
        "description": "Matches when symbol price is less than or equal to value.",
        "required": ["symbol", "value"],
    },
    {
        "type": "rapid_drop",
        "description": "Matches when symbol change over window is below negative threshold.",
        "required": ["symbol", "window_seconds", "change_pct"],
    },
    {
        "type": "rapid_rise",
        "description": "Matches when symbol change over window is above threshold.",
        "required": ["symbol", "window_seconds", "change_pct"],
    },
    {
        "type": "volume_multiple",
        "description": "Matches when current volume is at least multiplier times baseline.",
        "required": ["symbol", "timeframe", "multiplier"],
    },
]


@dataclass(frozen=True)
class SignalValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    normalized: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignalEvaluationResult:
    matched: bool
    valid: bool
    errors: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SignalExpressionEngine:
    """Small JSON-AST validator/evaluator for operator-defined signals."""

    def templates(self) -> list[dict[str, Any]]:
        return SIGNAL_TEMPLATES

    def validate(self, expression: dict[str, Any]) -> SignalValidationResult:
        errors: list[str] = []
        normalized = self._normalize(expression, "$", errors)
        return SignalValidationResult(valid=not errors, errors=errors, normalized=normalized)

    def evaluate(
        self,
        expression: dict[str, Any],
        *,
        context: dict[str, Any] | None = None,
    ) -> SignalEvaluationResult:
        validation = self.validate(expression)
        if not validation.valid:
            return SignalEvaluationResult(
                matched=False,
                valid=False,
                errors=validation.errors,
                evidence={"validation": validation.normalized},
            )
        matched, evidence = self._evaluate_node(validation.normalized, context or {})
        return SignalEvaluationResult(matched=matched, valid=True, evidence=evidence)

    def _normalize(self, expression: Any, path: str, errors: list[str]) -> dict[str, Any]:
        if not isinstance(expression, dict):
            errors.append(f"{path}: expression must be an object")
            return {}

        if "op" in expression:
            return self._normalize_composite(expression, path, errors)
        return self._normalize_primitive(expression, path, errors)

    def _normalize_composite(self, expression: dict[str, Any], path: str, errors: list[str]) -> dict[str, Any]:
        op = str(expression.get("op") or "").lower()
        if op not in {"and", "or"}:
            errors.append(f"{path}.op: must be 'and' or 'or'")
        raw_conditions = expression.get("conditions")
        if not isinstance(raw_conditions, list) or not raw_conditions:
            errors.append(f"{path}.conditions: must be a non-empty list")
            raw_conditions = []
        conditions = [
            self._normalize(condition, f"{path}.conditions[{index}]", errors)
            for index, condition in enumerate(raw_conditions)
        ]
        return {"op": op, "conditions": conditions}

    def _normalize_primitive(self, expression: dict[str, Any], path: str, errors: list[str]) -> dict[str, Any]:
        signal_type = str(expression.get("type") or "")
        template = {item["type"]: item for item in SIGNAL_TEMPLATES}.get(signal_type)
        if template is None:
            errors.append(f"{path}.type: unsupported signal type '{signal_type}'")
            return dict(expression)

        normalized = dict(expression)
        for key in template["required"]:
            if key not in normalized or normalized[key] in (None, ""):
                errors.append(f"{path}.{key}: required for {signal_type}")

        if signal_type in {"price_above", "price_below"}:
            self._require_number(normalized, "value", path, errors)
            self._require_symbol(normalized, path, errors)
        elif signal_type in {"rapid_drop", "rapid_rise"}:
            self._require_symbol(normalized, path, errors)
            self._require_positive_number(normalized, "window_seconds", path, errors)
            self._require_positive_number(normalized, "change_pct", path, errors)
        elif signal_type == "volume_multiple":
            self._require_symbol(normalized, path, errors)
            self._require_positive_number(normalized, "multiplier", path, errors)
            if not normalized.get("timeframe"):
                errors.append(f"{path}.timeframe: required for volume_multiple")
        return normalized

    def _require_symbol(self, expression: dict[str, Any], path: str, errors: list[str]) -> None:
        if not isinstance(expression.get("symbol"), str) or not expression["symbol"].strip():
            errors.append(f"{path}.symbol: must be a non-empty string")

    def _require_number(self, expression: dict[str, Any], key: str, path: str, errors: list[str]) -> None:
        try:
            expression[key] = float(expression[key])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{path}.{key}: must be numeric")

    def _require_positive_number(self, expression: dict[str, Any], key: str, path: str, errors: list[str]) -> None:
        self._require_number(expression, key, path, errors)
        if isinstance(expression.get(key), float) and expression[key] <= 0:
            errors.append(f"{path}.{key}: must be positive")

    def _evaluate_node(self, expression: dict[str, Any], context: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        if "op" in expression:
            results = [self._evaluate_node(item, context) for item in expression["conditions"]]
            matched_values = [matched for matched, _ in results]
            matched = all(matched_values) if expression["op"] == "and" else any(matched_values)
            return matched, {
                "op": expression["op"],
                "conditions": [evidence for _, evidence in results],
            }
        return self._evaluate_primitive(expression, context)

    def _evaluate_primitive(self, expression: dict[str, Any], context: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
        signal_type = expression["type"]
        symbol = expression.get("symbol")
        if signal_type in {"price_above", "price_below"}:
            price = self._lookup_nested_float(context, "prices", symbol)
            threshold = float(expression["value"])
            if price is None:
                return False, {"type": signal_type, "symbol": symbol, "missing": "price"}
            matched = price >= threshold if signal_type == "price_above" else price <= threshold
            return matched, {"type": signal_type, "symbol": symbol, "price": price, "threshold": threshold}

        if signal_type in {"rapid_drop", "rapid_rise"}:
            window = int(float(expression["window_seconds"]))
            change = self._lookup_nested_float(context, "changes_pct", f"{symbol}:{window}")
            threshold = float(expression["change_pct"])
            if change is None:
                return False, {"type": signal_type, "symbol": symbol, "window_seconds": window, "missing": "change_pct"}
            matched = change <= -threshold if signal_type == "rapid_drop" else change >= threshold
            return matched, {
                "type": signal_type,
                "symbol": symbol,
                "window_seconds": window,
                "change_pct": change,
                "threshold": threshold,
            }

        if signal_type == "volume_multiple":
            key = f"{symbol}:{expression['timeframe']}"
            ratio = self._lookup_nested_float(context, "volume_ratios", key)
            threshold = float(expression["multiplier"])
            if ratio is None:
                return False, {"type": signal_type, "symbol": symbol, "missing": "volume_ratio"}
            return ratio >= threshold, {"type": signal_type, "symbol": symbol, "volume_ratio": ratio, "threshold": threshold}

        return False, {"type": signal_type, "note": "validation-only primitive"}

    def _lookup_nested_float(self, context: dict[str, Any], key: str, nested_key: str | None) -> float | None:
        if nested_key is None:
            return None
        value = (context.get(key) or {}).get(nested_key)
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
