"""Pydantic v2 schemas for the Maybech REST API.

These models provide type-safe request/response contracts and auto-generate
OpenAPI documentation for the frontend.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


# ---------------------------------------------------------------------------
# Dynamic Position Rules
# ---------------------------------------------------------------------------

class PositionRuleBase(BaseModel):
    target: str = "self"
    metric: Literal["price", "pnl_pct", "velocity_1m", "velocity_5m", "velocity_10m"] = "price"
    operator: Literal["greater_than", "less_than"] = "less_than"
    value: float = 0.0


class PositionRuleCreate(PositionRuleBase):
    pass


class PositionRuleResponse(PositionRuleBase):
    id: str


class RuleGroupCreate(BaseModel):
    name: str = ""
    operator: Literal["and", "or"] = "and"
    rules: list[PositionRuleCreate] = Field(default_factory=list)


class RuleGroupResponse(BaseModel):
    id: str
    name: str
    operator: str
    rules: list[PositionRuleResponse]
    created_at: str


class TradeRuleResponse(BaseModel):
    """A RuleGroup attached to a specific trade."""
    group: RuleGroupResponse
    enabled: bool


class TradeRuleAttach(BaseModel):
    """Attach an existing or new RuleGroup to a trade."""
    rule_group: RuleGroupCreate
    enabled: bool = True


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------

class TradeResponse(BaseModel):
    id: str
    strategy_id: str
    inst_id: str
    side: str
    entry_price: float
    entry_time: str
    exit_price: float | None
    exit_time: str | None
    exit_reason: str
    pnl: float | None
    pnl_pct: float | None
    status: str
    signal_reason: str
    btc_price_at_entry: float | None
    btc_price_at_exit: float | None
    metadata_json: str


class TradeDetailResponse(TradeResponse):
    """Trade with its attached active rules."""
    active_rules: list[TradeRuleResponse] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

class PerformanceResponse(BaseModel):
    strategy_id: str = ""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0

