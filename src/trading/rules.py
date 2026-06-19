"""Dynamic exit rules for active positions.

Positions managed by the system can have dynamic exit conditions (rules).
A :class:`PositionRule` defines an atomic condition like "BTC price < 60000" or
"Position PnL > 5%".

A :class:`RuleGroup` combines multiple rules with AND / OR logic.
When a strategy opens a trade, its calculated stop-loss and take-profit
prices are automatically converted into two RuleGroups and attached to the position.
Users can then add, modify, or remove rule groups dynamically.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

# Rule target can be "self" (the traded instrument) or any specific instrument ID like "BTC-USDT-SWAP"
TargetType = str 

MetricType = Literal[
    "price",             # Current price of the target
    "pnl_pct",           # Unrealised PnL % of the position
    "velocity_1m",       # Percentage price change over last 1 minute
    "velocity_5m",       # Percentage price change over last 5 minutes
    "velocity_10m",      # Percentage price change over last 10 minutes
]

OperatorType = Literal[
    "greater_than",
    "less_than",
]

GroupOperator = Literal["and", "or"]


@dataclass
class PositionRule:
    """An atomic condition that is evaluated periodically."""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    target: TargetType = "self"
    metric: MetricType = "price"
    operator: OperatorType = "less_than"
    value: float = 0.0
    
    def evaluate(
        self,
        *,
        self_inst_id: str,
        prices: dict[str, float],
        velocities: dict[str, float],
        pnl_pct: float | None = None,
    ) -> bool:
        """Check whether this rule fires given current market state."""
        
        target_inst = self_inst_id if self.target == "self" else self.target

        # Resolve the current metric value
        current_value: float | None = None

        if self.metric == "price":
            current_value = prices.get(target_inst)
        elif self.metric == "pnl_pct":
            current_value = pnl_pct
        elif self.metric.startswith("velocity_"):
            key = f"{target_inst}:{self.metric}"
            current_value = velocities.get(key)

        if current_value is None:
            return False

        # Compare against threshold
        if self.operator == "greater_than":
            return current_value >= self.value
        elif self.operator == "less_than":
            return current_value <= self.value

        return False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PositionRule:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class RuleGroup:
    """One or more PositionRules combined with AND / OR logic."""

    id: str = field(default_factory=lambda: uuid4().hex[:12])
    name: str = ""
    operator: GroupOperator = "and"
    rules: list[PositionRule] = field(default_factory=list)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def evaluate(
        self,
        *,
        self_inst_id: str,
        prices: dict[str, float],
        velocities: dict[str, float],
        pnl_pct: float | None = None,
    ) -> bool:
        """Evaluate the group against current market data."""
        if not self.rules:
            return False

        results = [
            rule.evaluate(
                self_inst_id=self_inst_id,
                prices=prices,
                velocities=velocities,
                pnl_pct=pnl_pct,
            )
            for rule in self.rules
        ]

        if self.operator == "and":
            return all(results)
        return any(results)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "operator": self.operator,
            "rules": [r.to_dict() for r in self.rules],
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> RuleGroup:
        return cls(
            id=data.get("id", uuid4().hex[:12]),
            name=data.get("name", ""),
            operator=data.get("operator", "and"),
            rules=[PositionRule.from_dict(r) for r in data.get("rules", [])],
            created_at=data.get("created_at", datetime.now(timezone.utc).isoformat()),
        )
