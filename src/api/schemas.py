"""Pydantic v2 schemas for the Maybech REST API.

These models provide type-safe request/response contracts and auto-generate
OpenAPI documentation for the frontend.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Runtime Snapshots
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    ok: bool
    running: bool


class ServiceStatusResponse(BaseModel):
    name: str
    active: bool
    interval: float
    last_tick: str | None = None
    last_duration: str | None = None
    errors: int = 0


class RuntimeEventResponse(BaseModel):
    id: str
    type: str
    source: str
    created_at: str
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditEventResponse(RuntimeEventResponse):
    strategy_id: str | None = None
    correlation_id: str | None = None
    position_id: str | None = None
    trade_id: str | None = None


class AccountSnapshotResponse(BaseModel):
    summary: dict[str, Any] = Field(default_factory=dict)
    positions: list[dict[str, Any]] = Field(default_factory=list)
    orders: list[dict[str, Any]] = Field(default_factory=list)


class ExecutionFillIngestionStatusResponse(BaseModel):
    fetched: int = 0
    applied: int = 0
    idempotent: int = 0
    unmatched: int = 0
    invalid: int = 0
    conflicts: int = 0
    updated_at: str | None = None


class BTCRegimeResponse(BaseModel):
    symbol: str | None = None
    direction: str | None = None
    strength: str | None = None
    impulse: str | None = None
    price: float | None = None
    updated_at: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class StrategyDecisionResponse(BaseModel):
    id: str | None = None
    correlation_id: str | None = None
    strategy_id: str | None = None
    allowed: bool | None = None
    reason: str = ""
    pair: str | None = None
    signal: str | None = None
    time: str | None = None
    setup_reason: str | None = None
    entry_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    btc_direction: str | None = None
    btc_strength: str | None = None
    btc_impulse: str | None = None
    dry_run: bool | None = None
    execution_status: Literal[
        "pending",
        "blocked",
        "audit_failed",
        "simulated",
        "submitted",
        "failed",
        "persistence_failed",
    ] | str | None = None
    execution_result: dict[str, Any] = Field(default_factory=dict)
    order_id: str | None = None
    trade_id: str | None = None
    position_id: str | None = None
    persistence_error: str | None = None
    created_at: str | None = None
    completed_at: str | None = None


class PositionIntentResponse(BaseModel):
    inst_id: str = ""
    side: str = ""
    action: Literal["hold", "reduce", "close", "manual_review"] | str = ""
    reason: str = ""
    btc_direction: str | None = None
    btc_strength: str | None = None
    btc_impulse: str | None = None
    position_size: float | None = None
    unrealised_pnl_pct: float | None = None
    leverage: float | None = None
    liquidation_distance_pct: float | None = None


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
# Product-Facing Strategy and Logical Position Contracts
# ---------------------------------------------------------------------------

class StrategyRuntimeResponse(BaseModel):
    service: ServiceStatusResponse | None = None
    dry_run: bool | None = None
    latest_decisions: list[StrategyDecisionResponse] = Field(default_factory=list)


class SignalExpressionCreate(BaseModel):
    purpose: Literal["entry", "exit", "filter"] | str = "entry"
    expression: dict[str, Any] = Field(default_factory=dict)


class SignalTemplateResponse(BaseModel):
    type: str
    description: str
    required: list[str] = Field(default_factory=list)


class SignalValidationRequest(BaseModel):
    expression: dict[str, Any] = Field(default_factory=dict)


class SignalValidationResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    normalized: dict[str, Any] = Field(default_factory=dict)


class SignalEvaluationRequest(SignalValidationRequest):
    context: dict[str, Any] = Field(default_factory=dict)
    use_runtime_context: bool = False
    use_candle_context: bool = False
    symbols: list[str] = Field(default_factory=list)
    bar: str | None = None
    candle_limit: int = Field(default=120, ge=2, le=300)


class SignalEvaluationResponse(BaseModel):
    matched: bool
    valid: bool
    errors: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    evaluated_at: str


class SignalRuntimeContextResponse(BaseModel):
    prices: dict[str, float] = Field(default_factory=dict)
    changes_pct: dict[str, float] = Field(default_factory=dict)
    volume_ratios: dict[str, float] = Field(default_factory=dict)
    source: dict[str, Any] = Field(default_factory=dict)


class SignalExpressionResponse(SignalExpressionCreate):
    id: str
    strategy_id: str
    created_at: str
    updated_at: str


class LogicalPositionCloseConditionCreate(BaseModel):
    purpose: Literal["stop_loss", "take_profit", "trailing", "break_even", "manual_review", "exit"] | str = "exit"
    expression: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicalPositionCloseConditionUpdate(BaseModel):
    purpose: Literal["stop_loss", "take_profit", "trailing", "break_even", "manual_review", "exit"] | str | None = None
    expression: dict[str, Any] | None = None
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class LogicalPositionCloseConditionResponse(LogicalPositionCloseConditionCreate):
    id: str
    position_id: str
    created_at: str
    updated_at: str


class ConfirmedPositionFillCreate(BaseModel):
    fill_id: str = Field(min_length=1, max_length=128)
    action: Literal["open", "reduce", "close"]
    quantity: float = Field(gt=0)
    price: float = Field(gt=0)
    fee: float | None = None
    exchange_order_id: str = Field(default="", max_length=128)
    correlation_id: str = Field(default="", max_length=128)
    confirmation_source: Literal["okx_fill", "dry_run", "recovery"]
    occurred_at: str | None = None
    reason: str = "confirmed execution fill"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_exchange_order_for_okx_fill(self) -> "ConfirmedPositionFillCreate":
        if self.confirmation_source == "okx_fill" and not self.exchange_order_id:
            raise ValueError("exchange_order_id is required for okx_fill")
        return self


class LogicalPositionAllocationResponse(BaseModel):
    id: str
    position_id: str
    action: str
    quantity: float
    price: float | None = None
    fee: float | None = None
    exchange_order_id: str = ""
    reason: str = ""
    created_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConfirmedPositionFillResponse(BaseModel):
    allocation: LogicalPositionAllocationResponse
    idempotent: bool
    position_id: str
    trade_id: str | None = None
    status: str
    opened_quantity: float | None = None
    remaining_quantity: float | None = None
    average_entry_price: float
    execution_status: Literal["partially_filled", "filled", "reduced", "closed"] | str


class LogicalPositionCloseRequest(BaseModel):
    confirm: Literal[True]
    reason: str = Field(default="manual close", min_length=1, max_length=256)


class LogicalPositionCloseResponse(BaseModel):
    position_id: str
    trade_id: str | None = None
    inst_id: str
    side: str
    action: str
    reason: str | None = None
    exit_reason: str | None = None
    current_price: float | None = None
    quantity: float | None = None
    correlation_id: str | None = None
    exchange_order_id: str | None = None
    execution_status: str | None = None
    status: str | None = None


class StrategyCreate(BaseModel):
    id: str | None = None
    name: str
    kind: str = "custom"
    enabled: bool = False
    target_instruments: list[str] = Field(default_factory=list)
    entry_signal: dict[str, Any] = Field(default_factory=dict)
    default_rules: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class StrategyUpdate(BaseModel):
    name: str | None = None
    kind: str | None = None
    enabled: bool | None = None
    target_instruments: list[str] | None = None
    entry_signal: dict[str, Any] | None = None
    default_rules: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class StrategySummaryResponse(BaseModel):
    id: str
    name: str
    kind: str = "momentum"
    enabled: bool
    readiness: Literal["ready", "disabled", "blocked", "unknown"] = "unknown"
    target_instruments: list[str] = Field(default_factory=list)
    entry_signal: dict[str, Any] = Field(default_factory=dict)
    default_rules: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    signal_expressions: list[SignalExpressionResponse] = Field(default_factory=list)
    created_at: str | None = None
    updated_at: str | None = None
    runtime: StrategyRuntimeResponse = Field(default_factory=StrategyRuntimeResponse)


class LogicalPositionUnitResponse(BaseModel):
    id: str
    source: Literal["strategy", "manual", "import", "recovery", "unknown"] = "unknown"
    strategy_id: str | None = None
    trade_id: str | None = None
    inst_id: str
    side: str
    opened_quantity: float | None = None
    remaining_quantity: float | None = None
    entry_price: float
    entry_time: str
    status: str
    exchange_order_id: str = ""
    exchange_position_key: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    allocations: list[dict[str, Any]] = Field(default_factory=list)
    close_conditions: list[LogicalPositionCloseConditionResponse] = Field(default_factory=list)
    legacy_trade_rules: list[TradeRuleResponse] = Field(default_factory=list)
    current_intent: PositionIntentResponse | None = None
    reconciliation: dict[str, Any] | None = None
    okx_net_position: dict[str, Any] | None = None
    audit_events: list[RuntimeEventResponse] = Field(default_factory=list)


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

