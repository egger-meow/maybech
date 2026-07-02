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


class NotificationChannelHealthResponse(BaseModel):
    channel: Literal["line", "email"]
    configured: bool
    state: Literal["disabled", "unverified", "healthy", "failing", "backoff"]
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    last_failure_at: str | None = None
    last_error: str | None = None
    consecutive_failures: int = 0
    next_retry_at: str | None = None


class NotificationHealthResponse(BaseModel):
    service_enabled: bool
    backlog_count: int
    channels: list[NotificationChannelHealthResponse]
    checked_at: str


class NotificationTestRequest(BaseModel):
    confirm: Literal[True]
    channel: Literal["line", "email"]


class NotificationTestResponse(BaseModel):
    channel: Literal["line", "email"]
    success: bool
    state: Literal["healthy", "failing"]
    attempted_at: str
    error: str | None = None


class LivePreflightResponse(BaseModel):
    passed: bool
    armed: bool
    execution_mode: Literal["simulation", "demo", "live_safe", "live_armed"]
    exchange_enabled: bool = False
    order_submission_enabled: bool = False
    credential_environment: Literal["none", "demo", "production"] = "none"
    applicable_checks: list[str] = Field(default_factory=list)
    account_level: str = ""
    position_mode: str = ""
    account_scope: str = ""
    enabled_strategies: int = 0
    risk_limits_enabled: bool = False
    entries_enabled: bool = False
    instruments: list[str] = Field(default_factory=list)
    checked_at: str


class RuntimeLeaseResponse(BaseModel):
    held: bool
    owner_id: str = ""
    pid: int = 0
    hostname: str = ""
    database: str = ""
    account_scope: str = ""
    acquired_at: str = ""
    lock_root: str = ""


class RuntimeCapabilitiesResponse(BaseModel):
    role: Literal["combined", "replica"]
    storage_backend: Literal["sqlite"] = "sqlite"
    execution_leader: bool
    product_mutations_available: bool
    runtime_controls_available: bool
    live_runtime_snapshots_available: bool
    horizontal_read_replica: bool
    authentication_required: bool
    constraints: list[str] = Field(default_factory=list)


class AccountRiskLimitsUpdate(BaseModel):
    confirm: Literal[True]
    expected_updated_at: str | None = None
    enabled: bool
    max_order_notional_usd: float = Field(gt=0)
    max_total_exposure_usd: float = Field(gt=0)
    max_leverage: float = Field(gt=0, le=125)
    allowed_instruments: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_envelope(self) -> "AccountRiskLimitsUpdate":
        if self.max_order_notional_usd > self.max_total_exposure_usd:
            raise ValueError(
                "max_order_notional_usd cannot exceed max_total_exposure_usd"
            )
        normalized = list(dict.fromkeys(self.allowed_instruments))
        if self.enabled and not normalized:
            raise ValueError("allowed_instruments must not be empty when enabled")
        if any(not item or not item.endswith("-SWAP") for item in normalized):
            raise ValueError("allowed_instruments must contain OKX SWAP instrument ids")
        self.allowed_instruments = normalized
        return self


class AccountRiskLimitsResponse(BaseModel):
    enabled: bool
    max_order_notional_usd: float
    max_total_exposure_usd: float
    max_leverage: float
    allowed_instruments: list[str] = Field(default_factory=list)
    entries_enabled: bool = False
    created_at: str
    updated_at: str


class InstrumentMetadataResponse(BaseModel):
    inst_id: str
    inst_type: str
    state: str
    base_ccy: str = ""
    quote_ccy: str = ""
    settle_ccy: str = ""
    contract_type: str = ""
    contract_value: str = ""
    contract_currency: str = ""
    contract_multiplier: str = ""
    lot_size: str
    min_size: str
    tick_size: str
    size_precision: int
    price_precision: int
    max_limit_size: str = ""
    max_market_size: str = ""
    updated_at: str


class InstrumentMetadataListResponse(BaseModel):
    items: list[InstrumentMetadataResponse]
    refreshed_at: str
    refresh_due_at: str
    stale: bool


class InstrumentSizeQuoteRequest(BaseModel):
    display_quantity: str = Field(min_length=1, max_length=64)
    entry_price: str = Field(min_length=1, max_length=64)
    side: Literal["long", "short"]
    rule_price: str | None = Field(default=None, min_length=1, max_length=64)


class InstrumentContractQuoteRequest(BaseModel):
    api_quantity_contracts: str = Field(min_length=1, max_length=64)
    entry_price: str = Field(min_length=1, max_length=64)
    side: Literal["long", "short"]
    rule_price: str | None = Field(default=None, min_length=1, max_length=64)


class InstrumentSizeQuoteResponse(BaseModel):
    inst_id: str
    display_quantity: str
    display_currency: str
    api_quantity_contracts: str
    estimated_notional_usdt: str
    entry_price: str
    estimated_pnl_usdt: str | None = None


class InstrumentRiskQuoteRequest(BaseModel):
    mode: Literal["fixed_loss", "chart_anchored"]
    entry_price: str = Field(min_length=1, max_length=64)
    side: Literal["long", "short"]
    allowed_loss_usdt: str = Field(min_length=1, max_length=64)
    position_notional_usdt: str | None = Field(default=None, min_length=1, max_length=64)
    stop_price: str | None = Field(default=None, min_length=1, max_length=64)
    timeframe: str | None = Field(default=None, min_length=1, max_length=16)
    evidence: dict[str, Any] = Field(default_factory=dict)


class InstrumentRiskQuoteResponse(InstrumentSizeQuoteResponse):
    mode: Literal["fixed_loss", "chart_anchored"]
    allowed_loss_usdt: str
    stop_price: str
    stop_distance_pct: str
    estimated_loss_usdt: str
    unused_risk_usdt: str
    stop_expression: dict[str, Any]
    evidence: dict[str, Any]


class EntryControlCommand(BaseModel):
    confirm: Literal[True]


class EntryControlResponse(BaseModel):
    entries_enabled: bool
    process_entry_enabled: bool
    persisted: bool
    pending_entries: int = 0
    cancellations_requested: int = 0
    already_requested: int = 0
    already_terminal: int = 0
    unresolved: int = 0
    errors: list[str] = Field(default_factory=list)
    updated_at: str


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
    orders_checked: int = 0
    terminal_recovered: int = 0
    stale_cancel_requested: int = 0
    filled_awaiting_allocation: int = 0
    missing_fill_alerts: int = 0
    order_errors: int = 0
    client_orders_linked: int = 0
    missing_client_orders_recovered: int = 0
    protection_triggers_linked: int = 0
    protections_checked: int = 0
    protection_rearmed: int = 0
    protection_errors: int = 0
    pages_fetched: int = 0
    caught_up: bool = False
    cursor_in_progress: bool = False
    history_exhausted: bool = False
    high_water_bill_id: str = ""
    next_after_bill_id: str = ""
    cursor_errors: int = 0
    cursor_error: str = ""
    websocket_enabled: bool = False
    websocket_connected: bool = False
    websocket_events_received: int = 0
    websocket_events_processed: int = 0
    websocket_fills_applied: int = 0
    websocket_terminal_recovered: int = 0
    websocket_reconnects: int = 0
    websocket_dropped_events: int = 0
    websocket_last_message_at: str = ""
    websocket_last_error: str = ""
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
    pending_executions: list[dict[str, Any]] = Field(default_factory=list)


class SignalExpressionCreate(BaseModel):
    purpose: Literal["entry", "exit", "filter"] | str = "entry"
    expression: dict[str, Any] = Field(default_factory=dict)


class SignalExpressionCreateCommand(SignalExpressionCreate):
    confirm: Literal[True]
    expected_strategy_updated_at: str = Field(min_length=1)


class SignalExpressionUpdate(BaseModel):
    expected_updated_at: str = Field(min_length=1)
    purpose: Literal["entry", "exit", "filter"] | str | None = None
    expression: dict[str, Any] | None = None


class SignalExpressionDeleteCommand(BaseModel):
    confirm: Literal[True]
    expected_updated_at: str = Field(min_length=1)


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


class CandleResponse(BaseModel):
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    confirmed: bool


class MarketCandlesResponse(BaseModel):
    inst_id: str
    bar: str
    candles: list[CandleResponse] = Field(default_factory=list)
    fetched_at: str


class MarketAnalysisFreshnessResponse(BaseModel):
    evaluated_at: str
    latest_candle_at: str | None = None
    age_seconds: float | None = None
    stale_after_seconds: float | None = None
    stale: bool


class MarketAnalysisQualityResponse(BaseModel):
    input_candles: int
    usable_candles: int
    duplicate_candles: int
    missing_candles: int
    invalid_candles: int


class SupportResistanceLevelResponse(BaseModel):
    kind: Literal["support", "resistance"]
    state: Literal["active", "invalidated"]
    price: float
    score: float
    touches: int
    latest_touch_at: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class SupportResistanceAnalysisResponse(BaseModel):
    inst_id: str
    bar: str
    status: Literal["fresh", "partial", "unavailable"]
    freshness: MarketAnalysisFreshnessResponse
    quality: MarketAnalysisQualityResponse
    latest_price: float | None = None
    volatility_atr: float | None = None
    levels: list[SupportResistanceLevelResponse] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    cache_hit: bool = False
    research_only: Literal[True] = True
    eligible_as_live_rule: Literal[False] = False


class PositionChartOverlayResponse(BaseModel):
    kind: Literal[
        "entry",
        "current",
        "stop_loss",
        "take_profit",
        "break_even",
        "execution",
    ]
    price: float
    timestamp: str | None = None
    label: str
    allocation_id: str | None = None


class LogicalPositionChartResponse(MarketCandlesResponse):
    position_id: str
    overlays: list[PositionChartOverlayResponse] = Field(default_factory=list)


class SignalExpressionResponse(SignalExpressionCreate):
    id: str
    strategy_id: str
    created_at: str
    updated_at: str


class MutationStatusResponse(BaseModel):
    status: Literal["deleted", "ok"]
    id: str


class LogicalPositionCloseConditionCreate(BaseModel):
    purpose: Literal["stop_loss", "take_profit", "trailing", "break_even", "manual_review", "exit"] | str = "exit"
    expression: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class LogicalPositionCloseConditionCreateCommand(LogicalPositionCloseConditionCreate):
    confirm: Literal[True]
    expected_position_updated_at: str = Field(min_length=1)


class LogicalPositionCloseConditionUpdate(BaseModel):
    expected_updated_at: str = Field(min_length=1)
    purpose: Literal["stop_loss", "take_profit", "trailing", "break_even", "manual_review", "exit"] | str | None = None
    expression: dict[str, Any] | None = None
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class LogicalPositionCloseConditionDeleteCommand(BaseModel):
    confirm: Literal[True]
    expected_updated_at: str = Field(min_length=1)


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


class LogicalPositionProtectionResponse(BaseModel):
    position_id: str
    kind: Literal["attached_stop", "standalone_stop"]
    status: Literal[
        "active",
        "amending",
        "canceling",
        "canceled",
        "triggered",
        "exhausted",
        "failed",
    ]
    algo_id: str
    algo_client_order_id: str
    quantity: float
    stop_loss: float
    trigger_order_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class ConfirmedPositionFillResponse(BaseModel):
    allocation: LogicalPositionAllocationResponse
    idempotent: bool
    position_id: str
    trade_id: str | None = None
    status: str
    opened_quantity: float | None = None
    remaining_quantity: float | None = None
    average_entry_price: float
    execution_status: Literal[
        "partially_filled",
        "partially_reduced",
        "filled",
        "reduced",
        "closed",
    ] | str


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


class LogicalPositionReduceRequest(BaseModel):
    confirm: Literal[True]
    quantity: float = Field(gt=0)
    reason: str = Field(default="manual reduce", min_length=1, max_length=256)


class LogicalPositionReduceResponse(LogicalPositionCloseResponse):
    pass


class ExternalPositionImportRequest(BaseModel):
    confirm: Literal[True]
    inst_id: str = Field(min_length=1, max_length=64)
    side: Literal["long", "short"]
    close_conditions: list[LogicalPositionCloseConditionCreate] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=256)


class PositionProtectionCommand(BaseModel):
    confirm: Literal[True]


class PositionRecoveryAdoptionCommand(BaseModel):
    confirm: Literal[True]
    stop_loss: float = Field(gt=0)
    reason: str = Field(default="operator adopted recovered position", min_length=1, max_length=256)


class PositionStopAmendCommand(BaseModel):
    confirm: Literal[True]
    condition_id: str = Field(min_length=1, max_length=128)
    expression: dict[str, Any]
    reason: str = Field(min_length=1, max_length=256)


class PositionBreakEvenCommand(BaseModel):
    confirm: Literal[True]
    condition_id: str = Field(min_length=1, max_length=128)
    lock_in_pct: float = Field(default=0, ge=0, le=0.05)
    reason: str = Field(default="operator break-even", min_length=1, max_length=256)


class AccountExposureReconciliationResponse(BaseModel):
    safe_for_entries: bool
    state: Literal["balanced", "mismatch", "invalid", "protection_required"]
    groups: list[dict[str, Any]] = Field(default_factory=list)
    invalid_exchange_positions: list[str] = Field(default_factory=list)
    invalid_logical_positions: list[str] = Field(default_factory=list)
    unprotected_position_ids: list[str] = Field(default_factory=list)
    checked_at: str


class StrategyCreate(BaseModel):
    id: str | None = None
    name: str
    kind: str = "signal"
    enabled: bool = False
    target_instruments: list[str] = Field(default_factory=list)
    entry_signal: dict[str, Any] = Field(default_factory=dict)
    default_rules: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    execution_delay_seconds: int = Field(default=0, ge=0, le=86400)

    @model_validator(mode="after")
    def require_dedicated_enable(self) -> "StrategyCreate":
        if self.enabled:
            raise ValueError("Create strategies disabled, then use the confirmed enable endpoint")
        return self


class StrategyUpdate(BaseModel):
    expected_updated_at: str = Field(min_length=1)
    name: str | None = None
    kind: str | None = None
    enabled: bool | None = None
    target_instruments: list[str] | None = None
    entry_signal: dict[str, Any] | None = None
    default_rules: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    execution_delay_seconds: int | None = Field(default=None, ge=0, le=86400)

    @model_validator(mode="after")
    def require_dedicated_enable(self) -> "StrategyUpdate":
        if self.enabled is True:
            raise ValueError("Use the confirmed strategy enable endpoint")
        return self


class StrategyEnableCommand(BaseModel):
    confirm: Literal[True]
    expected_updated_at: str = Field(min_length=1)


class StrategyDeleteCommand(BaseModel):
    confirm: Literal[True]
    expected_updated_at: str = Field(min_length=1)


class StrategySummaryResponse(BaseModel):
    id: str
    name: str
    kind: str = "signal"
    enabled: bool
    readiness: Literal["ready", "disabled", "blocked", "unknown"] = "unknown"
    target_instruments: list[str] = Field(default_factory=list)
    entry_signal: dict[str, Any] = Field(default_factory=dict)
    default_rules: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    execution_delay_seconds: int = 0
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
    client_order_id: str = ""
    exchange_position_key: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str
    allocations: list[dict[str, Any]] = Field(default_factory=list)
    protection: LogicalPositionProtectionResponse | None = None
    close_conditions: list[LogicalPositionCloseConditionResponse] = Field(default_factory=list)
    legacy_trade_rules: list[TradeRuleResponse] = Field(default_factory=list)
    current_intent: PositionIntentResponse | None = None
    reconciliation: dict[str, Any] | None = None
    okx_net_position: dict[str, Any] | None = None
    audit_events: list[RuntimeEventResponse] = Field(default_factory=list)


class ManualPositionOpenRequest(BaseModel):
    confirm: Literal[True]
    inst_id: str = Field(min_length=1, max_length=64)
    side: Literal["long", "short"]
    display_quantity: str = Field(min_length=1, max_length=64)
    entry_price: str = Field(min_length=1, max_length=64)
    stop_loss_price: str = Field(min_length=1, max_length=64)
    take_profit_price: str | None = Field(default=None, min_length=1, max_length=64)


class PositionGroupResponse(BaseModel):
    key: str
    group_by: Literal["instrument_side", "strategy", "exchange_position"]
    inst_id: str | None = None
    side: str | None = None
    strategy_id: str | None = None
    exchange_position_key: str | None = None
    position_ids: list[str] = Field(default_factory=list)
    position_count: int
    active_count: int
    opened_quantity: float
    remaining_quantity: float
    weighted_entry_price: float | None = None
    statuses: dict[str, int] = Field(default_factory=dict)


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

