"""FastAPI adapter for the Maybech daemon runtime."""

from __future__ import annotations

import asyncio
from asyncio import QueueEmpty
from datetime import datetime, timezone
from decimal import Decimal
import json
import secrets
import sqlite3
from typing import Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config.settings import settings
from src.data.candles import CandleManager
from src.data.simulation_market import SimulationMarketClient
from src.market.support_resistance import SupportResistanceService
from src.daemon.events import RuntimeEvent
from src.daemon.service import DaemonRunner
from src.exchange.client import OKXClient, entry_order_placement_enabled
from src.notifications.email_alert import EmailNotifier
from src.notifications.line_bot import LineBotNotifier

from src.trading.account_risk import AccountRiskLimits, AccountRiskStore
from src.trading.audit_event_store import AuditEventRecord, AuditEventStore
from src.trading.execution_allocation import (
    ConfirmedExecutionFill,
    ExecutionAllocationService,
)
from src.trading.entry_control import ENTRY_EXECUTION_LOCK, EntryControlManager
from src.trading.logical_position_store import (
    AllocationConflictError,
    LogicalPositionAllocation,
    LogicalPositionCloseCondition,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.position_reconciliation import PositionReconciliation, PositionReconciler
from src.trading.position_import import (
    PositionImportConflict,
    PositionImportRequest,
    PositionImportService,
)
from src.trading.instrument_metadata import InstrumentMetadataStore
from src.trading.instrument_sizing import InstrumentSizer
from src.trading.position_protection import (
    PositionProtectionError,
    PositionProtectionService,
)
from src.trading.rules import PositionRule, RuleGroup
from src.trading.signal_context import build_signal_context_from_candles, collect_signal_requirements
from src.trading.signal_engine import SignalExpressionEngine
from src.trading.sqlite_schema import reset_sqlite_read_only, set_sqlite_read_only
from src.trading.strategy_runtime import validate_strategy_for_execution
from src.trading.strategy_store import SignalExpressionRecord, StrategyRecord, StrategyStore
from src.trading.trade_store import TradeStore
from src.api.schemas import (
    AccountExposureReconciliationResponse,
    AccountRiskLimitsResponse,
    AccountRiskLimitsUpdate,
    AccountSnapshotResponse,
    AuditEventResponse,
    BTCRegimeResponse,
    ConfirmedPositionFillCreate,
    ConfirmedPositionFillResponse,
    ExecutionFillIngestionStatusResponse,
    EntryControlCommand,
    EntryControlResponse,
    ExternalPositionImportRequest,
    HealthResponse,
    InstrumentMetadataListResponse,
    InstrumentMetadataResponse,
    InstrumentContractQuoteRequest,
    InstrumentSizeQuoteRequest,
    InstrumentSizeQuoteResponse,
    InstrumentRiskQuoteRequest,
    InstrumentRiskQuoteResponse,
    StrategyRiskStopPromotionCommand,
    PositionRiskStopPromotionCommand,
    LivePreflightResponse,
    LogicalPositionUnitResponse,
    ManualPositionOpenRequest,
    NotificationHealthResponse,
    NotificationTestRequest,
    NotificationTestResponse,
    LogicalPositionCloseConditionCreateCommand,
    LogicalPositionCloseConditionDeleteCommand,
    LogicalPositionCloseConditionResponse,
    LogicalPositionCloseConditionUpdate,
    LogicalPositionCloseRequest,
    LogicalPositionCloseResponse,
    LogicalPositionChartResponse,
    LogicalPositionReduceRequest,
    LogicalPositionReduceResponse,
    LogicalPositionAllocationResponse,
    MutationStatusResponse,
    MarketCandlesResponse,
    SupportResistanceAnalysisResponse,
    PositionGroupResponse,
    PositionChartOverlayResponse,
    PositionIntentResponse,
    PositionBreakEvenCommand,
    PositionProtectionCommand,
    PositionRecoveryAdoptionCommand,
    PositionStopAmendCommand,
    PositionRuleResponse,
    RuleGroupResponse,
    RuntimeLeaseResponse,
    RuntimeCapabilitiesResponse,
    RuntimeEventResponse,
    ServiceStatusResponse,
    SignalEvaluationRequest,
    SignalEvaluationResponse,
    SignalExpressionCreateCommand,
    SignalExpressionDeleteCommand,
    SignalExpressionResponse,
    SignalExpressionUpdate,
    SignalRuntimeContextResponse,
    SignalTemplateResponse,
    SignalValidationRequest,
    SignalValidationResponse,
    StrategyCreate,
    StrategyDeleteCommand,
    StrategyEnableCommand,
    StrategyDecisionResponse,
    StrategyRuntimeResponse,
    StrategySummaryResponse,
    StrategyUpdate,
    TradeDetailResponse,
    TradeResponse,
    TradeRuleAttach,
    TradeRuleResponse,
)


def _serialize_event(event: RuntimeEvent) -> dict:
    return {
        "id": event.id,
        "type": event.type,
        "source": event.source,
        "created_at": event.created_at.isoformat(),
        "payload": event.payload,
    }


def _audit_event_response(event: AuditEventRecord) -> AuditEventResponse:
    return AuditEventResponse(**event.to_dict())


def _invalidate_external_position_protection(
    position_store: LogicalPositionStore,
    position: LogicalPositionRecord,
) -> None:
    if position.source in {"import", "recovery"}:
        position_store.merge_metadata(
            position.id,
            {
                "exchange_protection_verified": False,
                "exchange_protection_error": "close conditions changed; verification required",
            },
        )


def _protected_stop_mutation_blocked(
    position_store: LogicalPositionStore,
    position: LogicalPositionRecord,
    *,
    condition: LogicalPositionCloseCondition | None = None,
    purpose: str | None = None,
    enabled: bool | None = None,
    definition_changed: bool = True,
) -> bool:
    protection = position_store.get_protection(position.id)
    if protection is None or protection.status in {"canceled", "exhausted"}:
        return False
    if not definition_changed:
        return False
    old_is_stop = bool(
        condition is not None
        and condition.purpose == "stop_loss"
        and condition.enabled
    )
    next_purpose = purpose if purpose is not None else (
        condition.purpose if condition is not None else "exit"
    )
    next_enabled = enabled if enabled is not None else (
        condition.enabled if condition is not None else True
    )
    return old_is_stop or (next_purpose == "stop_loss" and next_enabled)


def _raise_protected_stop_edit_conflict() -> None:
    raise HTTPException(
        status_code=409,
        detail=(
            "Owned protective stops must be edited through the confirmed "
            "protection stop-amend endpoint"
        ),
    )


def _strategy_decision_response(event: AuditEventRecord) -> StrategyDecisionResponse:
    data = dict(event.payload)
    data.setdefault("id", event.id)
    data.setdefault("correlation_id", event.correlation_id or event.id)
    data.setdefault("strategy_id", event.strategy_id or None)
    data.setdefault("created_at", event.created_at)
    return StrategyDecisionResponse(**data)


def _serialize_status(status: Optional[dict]) -> Optional[dict]:
    if status is None:
        return None
    serialized = dict(status)
    for key, value in list(serialized.items()):
        if isinstance(value, datetime):
            serialized[key] = value.isoformat()
    return serialized


def _rule_group_response(group: RuleGroup) -> RuleGroupResponse:
    return RuleGroupResponse(
        id=group.id,
        name=group.name,
        operator=group.operator,
        created_at=group.created_at,
        rules=[PositionRuleResponse(**rule.to_dict()) for rule in group.rules],
    )


def _trade_rule_responses(store: TradeStore, trade_id: str) -> list[TradeRuleResponse]:
    return [
        TradeRuleResponse(group=_rule_group_response(group), enabled=enabled)
        for group, enabled in store.get_trade_rules(trade_id)
    ]


def _close_condition_response(
    condition: LogicalPositionCloseCondition,
) -> LogicalPositionCloseConditionResponse:
    return LogicalPositionCloseConditionResponse(
        id=condition.id,
        position_id=condition.position_id,
        purpose=condition.purpose,
        expression=condition.expression,
        enabled=condition.enabled,
        metadata=condition.metadata,
        rule_definition=condition.metadata["rule_definition"],
        created_at=condition.created_at,
        updated_at=condition.updated_at,
    )


def _allocation_response(
    allocation: LogicalPositionAllocation,
) -> LogicalPositionAllocationResponse:
    try:
        metadata = json.loads(allocation.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {"raw": allocation.metadata_json}
    return LogicalPositionAllocationResponse(
        id=allocation.id,
        position_id=allocation.position_id,
        action=allocation.action,
        quantity=allocation.quantity,
        price=allocation.price,
        fee=allocation.fee,
        exchange_order_id=allocation.exchange_order_id,
        reason=allocation.reason,
        created_at=allocation.created_at,
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def _normalize_position_side(side: object) -> str:
    normalized = str(side or "").lower()
    if normalized in {"long", "buy"}:
        return "long"
    if normalized in {"short", "sell"}:
        return "short"
    return normalized


def _position_inst_id(position: dict) -> str:
    return str(position.get("inst_id") or position.get("instId") or "")


def _position_side(position: dict) -> str:
    return _normalize_position_side(position.get("pos_side") or position.get("posSide") or position.get("side"))


def _find_matching_okx_position(trade: object, positions: list[dict]) -> dict | None:
    fallback = None
    for position in positions:
        if _position_inst_id(position) != trade.inst_id:
            continue
        if fallback is None:
            fallback = position
        side = _position_side(position)
        if side and side == _normalize_position_side(trade.side):
            return position
    return fallback


def _find_matching_intent(position: object, intents: list[dict]) -> PositionIntentResponse | None:
    for intent in intents:
        if str(intent.get("inst_id") or intent.get("instId") or "") != position.inst_id:
            continue
        side = _normalize_position_side(intent.get("side"))
        if side and side != _normalize_position_side(position.side):
            continue
        return PositionIntentResponse(**intent)
    return None


def _logical_position_response(
    *,
    store: TradeStore,
    position_store: LogicalPositionStore,
    position: LogicalPositionRecord,
    account_snapshot: dict,
    intents: list[dict],
    audit_events: list[RuntimeEvent],
    reconciliation: PositionReconciliation | None = None,
) -> LogicalPositionUnitResponse:
    rule_owner_id = position.trade_id or position.id
    try:
        metadata = json.loads(position.metadata_json or "{}")
    except json.JSONDecodeError:
        metadata = {"raw": position.metadata_json}
    matching_events = [
        RuntimeEventResponse(**_serialize_event(event))
        for event in audit_events
        if event.payload.get("trade_id") == rule_owner_id or event.payload.get("position_id") == position.id
    ]
    protection = position_store.get_protection(position.id)
    return LogicalPositionUnitResponse(
        id=position.id,
        source=position.source,
        strategy_id=position.strategy_id or None,
        trade_id=position.trade_id,
        inst_id=position.inst_id,
        side=_normalize_position_side(position.side) or position.side,
        opened_quantity=position.opened_quantity,
        remaining_quantity=position.remaining_quantity,
        entry_price=position.entry_price,
        entry_time=position.entry_time,
        status=position.status,
        exchange_order_id=position.exchange_order_id,
        client_order_id=position.client_order_id,
        exchange_position_key=position.exchange_position_key,
        metadata=metadata,
        created_at=position.created_at,
        updated_at=position.updated_at,
        allocations=[allocation.to_dict() for allocation in position_store.list_allocations(position.id)],
        protection=None if protection is None else protection.to_dict(),
        close_conditions=[
            _close_condition_response(condition)
            for condition in position_store.list_close_conditions(position.id)
        ],
        legacy_trade_rules=_trade_rule_responses(store, rule_owner_id),
        current_intent=_find_matching_intent(position, intents),
        reconciliation=None if reconciliation is None else reconciliation.to_dict(),
        okx_net_position=_find_matching_okx_position(position, account_snapshot.get("positions", [])),
        audit_events=matching_events,
    )


def _backfill_logical_positions(
    *,
    trade_store: TradeStore,
    position_store: LogicalPositionStore,
    status: str,
    strategy_id: str | None,
    limit: int,
) -> None:
    if status == "open":
        trades = trade_store.get_open_trades()
        if strategy_id:
            trades = [trade for trade in trades if trade.strategy_id == strategy_id]
    elif status == "all":
        trades = trade_store.get_trade_history(limit=limit, strategy_id=strategy_id)
    else:
        trades = trade_store.get_trade_history(limit=limit, strategy_id=strategy_id, status=status)

    for trade in trades:
        position_store.ensure_from_trade(trade)


def _get_or_backfill_logical_position(
    *,
    trade_store: TradeStore,
    position_store: LogicalPositionStore,
    position_id: str,
) -> LogicalPositionRecord | None:
    position = position_store.get(position_id)
    if position is not None:
        return position
    trade = trade_store.get_trade(position_id)
    if trade is None:
        return None
    return position_store.ensure_from_trade(trade)


def _validate_signal_or_400(expression: dict) -> None:
    validation = SignalExpressionEngine().validate(expression)
    if not validation.valid:
        raise HTTPException(
            status_code=400,
            detail={"message": "Signal expression validation failed", "errors": validation.errors},
        )


def _signal_expression_response(expression: SignalExpressionRecord) -> SignalExpressionResponse:
    return SignalExpressionResponse(
        id=expression.id,
        strategy_id=expression.strategy_id,
        purpose=expression.purpose,
        expression=expression.expression,
        created_at=expression.created_at,
        updated_at=expression.updated_at,
    )


def _strategy_summary(
    runner: DaemonRunner,
    strategy: StrategyRecord,
    store: StrategyStore,
) -> StrategySummaryResponse:
    status = _serialize_status(runner.get_service_status("strategy"))
    service = None if status is None else ServiceStatusResponse(**status)
    strategy_service = runner.services.get("strategy")
    latest_decisions = runner.runtime.get_value("strategy.decisions") or []
    service_active = bool(service.active) if service is not None else False
    enabled = strategy.enabled
    validation_errors = validate_strategy_for_execution(strategy, store)
    readiness = (
        "blocked"
        if enabled and validation_errors
        else "ready"
        if enabled and service_active
        else "disabled"
    )
    return StrategySummaryResponse(
        id=strategy.id,
        name=strategy.name,
        kind=strategy.kind,
        enabled=enabled,
        readiness=readiness,
        target_instruments=strategy.target_instruments,
        entry_signal=strategy.entry_signal,
        default_rules=strategy.default_rules,
        metadata=strategy.metadata,
        execution_delay_seconds=strategy.execution_delay_seconds,
        signal_expressions=[
            _signal_expression_response(expression)
            for expression in store.list_signal_expressions(strategy.id)
        ],
        created_at=strategy.created_at,
        updated_at=strategy.updated_at,
        runtime=StrategyRuntimeResponse(
            service=service,
            dry_run=getattr(strategy_service, "dry_run", None),
            latest_decisions=[StrategyDecisionResponse(**decision) for decision in latest_decisions],
            pending_executions=[
                pending.to_dict()
                for pending in store.list_pending_executions(strategy_id=strategy.id)
            ],
        ),
    )


def _signal_expression_payload(expression: SignalExpressionRecord) -> dict:
    return _signal_expression_response(expression).model_dump()


def _strategy_definition_payload(strategy: StrategyRecord) -> dict:
    return {
        "id": strategy.id,
        "name": strategy.name,
        "kind": strategy.kind,
        "enabled": strategy.enabled,
        "target_instruments": strategy.target_instruments,
        "entry_signal": strategy.entry_signal,
        "default_rules": strategy.default_rules,
        "metadata": strategy.metadata,
        "execution_delay_seconds": strategy.execution_delay_seconds,
        "created_at": strategy.created_at,
        "updated_at": strategy.updated_at,
    }


def _record_definition_audit(
    audit_store: AuditEventStore,
    *,
    event_type: str,
    payload: dict,
    connection: sqlite3.Connection,
) -> None:
    audit_store.create(
        type=event_type,
        source="product_api",
        payload=payload,
        connection=connection,
    )


def _strategy_validation_errors(strategy: StrategyRecord, store: StrategyStore) -> list[str]:
    errors = validate_strategy_for_execution(strategy, store)
    risk_limits = AccountRiskStore(store.db_path, initialize=False).get()
    if risk_limits is not None:
        outside = sorted(
            set(strategy.target_instruments) - set(risk_limits.allowed_instruments)
        )
        if outside:
            errors.append(
                "strategy targets outside account risk allowlist: "
                + ", ".join(outside)
            )
    return errors


def _as_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _merge_signal_context(base: dict, override: dict) -> dict:
    merged = {
        "prices": dict(base.get("prices") or {}),
        "changes_pct": dict(base.get("changes_pct") or {}),
        "volume_ratios": dict(base.get("volume_ratios") or {}),
        "source": dict(base.get("source") or {}),
    }
    for key, value in override.items():
        if key in {"prices", "changes_pct", "volume_ratios", "source"} and isinstance(value, dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def _signal_runtime_context(runner: DaemonRunner) -> dict:
    prices: dict[str, float] = {}
    changes_pct: dict[str, float] = {}
    source: dict[str, object] = {"runtime": True}

    regime = runner.runtime.get_value("market.btc_regime") or {}
    if regime:
        symbol = str(regime.get("symbol") or "BTC-USDT-SWAP")
        price = _as_float(regime.get("price"))
        if price is not None:
            prices[symbol] = price
        change_pct = _as_float(regime.get("change_pct"))
        if change_pct is not None:
            changes_pct[f"{symbol}:60"] = change_pct
        source["btc_regime"] = {"available": True, "symbol": symbol}
    else:
        source["btc_regime"] = {"available": False}

    snapshot = runner.runtime.get_value("account.snapshot") or {}
    positions = snapshot.get("positions", []) if isinstance(snapshot, dict) else []
    for position in positions:
        if not isinstance(position, dict):
            continue
        inst_id = str(position.get("inst_id") or position.get("instId") or "")
        mark_price = _as_float(position.get("mark_price") or position.get("markPx"))
        if inst_id and mark_price is not None:
            prices[inst_id] = mark_price
    source["account_positions"] = {"count": len(positions)}
    return {
        "prices": prices,
        "changes_pct": changes_pct,
        "volume_ratios": {},
        "source": source,
    }


def _normalize_symbols(symbols: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for symbol in symbols or []:
        clean = symbol.strip()
        if clean and clean not in normalized:
            normalized.append(clean)
    return normalized


def _parse_symbols_param(symbols: str | None) -> list[str]:
    if not symbols:
        return []
    return _normalize_symbols(symbols.split(","))


def _as_swap_symbol(symbol: str) -> str:
    clean = symbol.strip()
    if clean.endswith("-SWAP"):
        return clean
    if clean.endswith("-USDT"):
        return f"{clean}-SWAP"
    return f"{clean}-USDT-SWAP"


def _signal_candle_context(
    *,
    expression: dict | None = None,
    symbols: list[str] | None = None,
    bar: str | None = None,
    limit: int = 120,
    windows_seconds: set[int] | None = None,
    client=None,
) -> dict:
    requirements = collect_signal_requirements(expression or {})
    strategy_store = StrategyStore()
    configured_symbols = {
        instrument
        for strategy in strategy_store.list(enabled=True)
        for instrument in strategy.target_instruments
    }
    requested_symbols = _normalize_symbols(
        [
            *list(requirements["symbols"]),
            *list(symbols or []),
        ]
    )
    if not requested_symbols:
        requested_symbols = _normalize_symbols(configured_symbols or {"BTC-USDT-SWAP"})

    requested_bar = (
        bar
        or next(iter(requirements["timeframes"]), None)
        or "1m"
    )
    requested_windows = set(windows_seconds or set())
    requested_windows.update(requirements["windows_seconds"])
    if not requested_windows:
        requested_windows.update({60, 300, 600})

    try:
        manager = CandleManager(client or OKXClient())
        candles = {
            symbol: manager.fetch(symbol, bar=requested_bar, limit=limit)
            for symbol in requested_symbols
        }
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch candle context: {exc}") from exc

    context = build_signal_context_from_candles(
        candles,
        bar=requested_bar,
        windows_seconds=requested_windows,
    )
    context["source"]["candles"].update(
        {
            "requested_symbols": requested_symbols,
            "limit": limit,
            "windows_seconds": sorted(requested_windows),
        }
    )
    return context


def _fetch_candle_rows(inst_id: str, *, bar: str, limit: int, client=None) -> list[dict]:
    try:
        frame = CandleManager(client or OKXClient()).fetch(inst_id, bar=bar, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Unable to fetch candles: {exc}") from exc

    candles: list[dict] = []
    for _, row in frame.iterrows():
        timestamp = row["timestamp"]
        candles.append(
            {
                "timestamp": (
                    timestamp.isoformat()
                    if hasattr(timestamp, "isoformat")
                    else str(timestamp)
                ),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
                "confirmed": bool(int(row.get("confirm", 1))),
            }
        )
    return candles


def _position_chart_overlays(
    position_store: LogicalPositionStore,
    position: LogicalPositionRecord,
    candles: list[dict],
) -> list[PositionChartOverlayResponse]:
    overlays = [
        PositionChartOverlayResponse(
            kind="entry",
            price=position.entry_price,
            timestamp=position.entry_time,
            label="Entry",
        )
    ]
    if candles:
        overlays.append(
            PositionChartOverlayResponse(
                kind="current",
                price=float(candles[-1]["close"]),
                timestamp=str(candles[-1]["timestamp"]),
                label="Current",
            )
        )

    for condition in position_store.list_close_conditions(position.id, enabled=True):
        if condition.purpose not in {"stop_loss", "take_profit", "break_even", "trailing"}:
            continue
        value = _as_float(condition.expression.get("value"))
        if value is not None and value > 0:
            overlays.append(
                PositionChartOverlayResponse(
                    kind=condition.purpose,
                    price=value,
                    label=condition.purpose.replace("_", " ").title(),
                )
            )
        break_even = condition.metadata.get("break_even")
        if isinstance(break_even, dict):
            target = _as_float(break_even.get("target_stop"))
            if target is not None and target > 0:
                overlays.append(
                    PositionChartOverlayResponse(
                        kind="break_even",
                        price=target,
                        timestamp=str(break_even.get("applied_at") or "") or None,
                        label="Break Even",
                    )
                )
        trailing = condition.metadata.get("trailing_state")
        if isinstance(trailing, dict):
            candidate = _as_float(trailing.get("candidate_price"))
            if candidate is not None and candidate > 0:
                overlays.append(
                    PositionChartOverlayResponse(
                        kind="trailing",
                        price=candidate,
                        timestamp=str(trailing.get("updated_at") or "") or None,
                        label=(
                            "Trailing Stop Candidate"
                            if trailing.get("kind") == "stop"
                            else "Trailing Take Profit Candidate"
                        ),
                    )
                )

    for allocation in position_store.list_allocations(position.id):
        if allocation.price is None or allocation.price <= 0:
            continue
        overlays.append(
            PositionChartOverlayResponse(
                kind="execution",
                price=allocation.price,
                timestamp=allocation.created_at,
                label=allocation.action.replace("_", " ").title(),
                allocation_id=allocation.id,
            )
        )
    return overlays


def create_app(
    runner: DaemonRunner,
    *,
    runtime_role: Literal["combined", "replica"] = "combined",
    api_token: str | None = None,
) -> FastAPI:
    """Create an API app bound to a daemon runner."""
    def runtime_mode() -> str:
        status = runner.runtime.get_value("runtime.live_preflight") or {}
        mode = str(status.get("execution_mode") or "unknown")
        return {"dry_run": "simulation", "real": "live_armed"}.get(mode, mode)

    def market_client():
        return SimulationMarketClient() if runtime_mode() == "simulation" else OKXClient()

    support_resistance = SupportResistanceService(market_client)

    def exchange_client(*, require_orders: bool = False):
        mode = runtime_mode()
        if mode == "simulation":
            raise HTTPException(status_code=409, detail="Simulation does not connect to OKX")
        if require_orders and mode == "live_safe":
            raise HTTPException(status_code=409, detail="Live Safe disables all order mutations")
        return OKXClient()

    def build_risk_quote(inst_id: str, payload: InstrumentRiskQuoteRequest):
        metadata_store = InstrumentMetadataStore()
        metadata = metadata_store.get(inst_id)
        if metadata is None:
            raise HTTPException(
                status_code=404,
                detail=f"Cached OKX metadata for {inst_id} is unavailable",
            )
        if metadata_store.cache_status(inst_type="SWAP")["stale"]:
            raise HTTPException(
                status_code=409,
                detail="Cached OKX instrument metadata is stale; refresh is required",
            )
        try:
            return InstrumentSizer(metadata).quote_risk(
                mode=payload.mode,
                entry_price=payload.entry_price,
                side=payload.side,
                allowed_loss_usdt=payload.allowed_loss_usdt,
                position_notional_usdt=payload.position_notional_usdt,
                stop_price=payload.stop_price,
                timeframe=payload.timeframe,
                evidence=payload.evidence,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    def require_promotable_research(payload: InstrumentRiskQuoteRequest) -> None:
        evidence = payload.evidence
        if "selected_research_level" not in evidence:
            return
        blockers = []
        if evidence.get("analysis_state") != "fresh":
            blockers.append("analysis is not fresh")
        if evidence.get("level_state") != "active":
            blockers.append("level is missing or invalidated")
        if evidence.get("btc_regime_alignment") == "conflicting":
            blockers.append("BTC regime evidence conflicts")
        if blockers:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Research proposal requires manual review",
                    "state": "manual_review",
                    "blockers": blockers,
                },
            )

    app = FastAPI(title="Maybech Runtime API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.MAYBECH_CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.state.runner = runner
    app.state.runtime_role = runtime_role
    selected_api_token = settings.MAYBECH_API_TOKEN if api_token is None else api_token
    app.state.authentication_required = bool(selected_api_token)

    @app.middleware("http")
    async def enforce_api_authentication(request: Request, call_next):
        if (
            selected_api_token
            and request.method != "OPTIONS"
            and request.url.path not in {"/health", "/runtime/capabilities"}
        ):
            authorization = request.headers.get("Authorization", "")
            scheme, _, credential = authorization.partition(" ")
            if scheme.lower() != "bearer" or not secrets.compare_digest(
                credential,
                selected_api_token,
            ):
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Valid bearer authentication is required"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    @app.middleware("http")
    async def enforce_runtime_role(request: Request, call_next):
        if runtime_role == "replica":
            leader_only_reads = {
                "/account/exposure-reconciliation",
                "/account/orders",
                "/account/positions",
                "/account/snapshot",
                "/events",
                "/execution/fills/status",
                "/market/btc-regime",
                "/position/intents",
                "/strategy/decisions",
            }
            leader_only_prefixes = ("/services",)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                detail = (
                    "API replica is read-only; route mutations to the combined "
                    "execution leader"
                )
            elif request.url.path in leader_only_reads or request.url.path.startswith(
                leader_only_prefixes
            ):
                detail = "Live runtime data is available only from the execution leader"
            else:
                detail = ""
            if detail:
                return JSONResponse(status_code=503, content={"detail": detail})
        read_only_token = set_sqlite_read_only(runtime_role == "replica")
        try:
            return await call_next(request)
        finally:
            reset_sqlite_read_only(read_only_token)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return {"ok": True, "running": runner.running}

    @app.get("/notifications/health", response_model=NotificationHealthResponse)
    def get_notification_health() -> NotificationHealthResponse:
        store = AuditEventStore()
        persisted = store.notification_delivery_health("lifecycle_notifications")
        now = datetime.now(timezone.utc)
        configured = {
            "line": bool(
                settings.LINE_CHANNEL_ACCESS_TOKEN
                and settings.LINE_CHANNEL_SECRET
                and settings.LINE_USER_ID
            ),
            "email": bool(
                settings.EMAIL_SENDER
                and settings.EMAIL_PASSWORD
                and settings.EMAIL_RECEIVER
            ),
        }
        channels = []
        for channel in ("line", "email"):
            record = persisted.get(channel, {})
            if not configured[channel]:
                state = "disabled"
            elif int(record.get("consecutive_failures") or 0) > 0:
                retry_at = str(record.get("next_retry_at") or "")
                state = "backoff" if retry_at and retry_at > now.isoformat() else "failing"
            elif record.get("last_success_at"):
                state = "healthy"
            else:
                state = "unverified"
            channels.append(
                {
                    "channel": channel,
                    "configured": configured[channel],
                    "state": state,
                    "last_attempt_at": record.get("last_attempt_at") or None,
                    "last_success_at": record.get("last_success_at") or None,
                    "last_failure_at": record.get("last_failure_at") or None,
                    "last_error": record.get("last_error") or None,
                    "consecutive_failures": int(record.get("consecutive_failures") or 0),
                    "next_retry_at": record.get("next_retry_at") or None,
                }
            )
        service = runner.get_service_status("lifecycle_notifications")
        return NotificationHealthResponse(
            service_enabled=bool(service and service.get("active", False)),
            backlog_count=store.pending_delivery_count("lifecycle_notifications"),
            channels=channels,
            checked_at=now.isoformat(),
        )

    @app.post("/notifications/test", response_model=NotificationTestResponse)
    def test_notification(payload: NotificationTestRequest) -> NotificationTestResponse:
        store = AuditEventStore()
        event_id = f"notification-test-{uuid4().hex}"
        attempted_at = datetime.now(timezone.utc).isoformat()
        message = f"Maybech｜通知測試\n通道：{payload.channel}\n時間：{attempted_at}"
        notifier = LineBotNotifier() if payload.channel == "line" else EmailNotifier()
        if not notifier.enabled:
            raise HTTPException(
                status_code=409,
                detail=f"{payload.channel} notification channel is not configured",
            )
        store.create(
            id=event_id,
            type="notification.test_requested",
            source="product_api",
            payload={"channel": payload.channel},
            created_at=attempted_at,
        )
        success = (
            notifier.send(message)
            if payload.channel == "line"
            else notifier.send("Maybech｜通知測試", message)
        )
        error = "" if success else str(notifier.last_error or "transport returned failure")
        store.record_notification_delivery_attempt(
            "lifecycle_notifications",
            payload.channel,
            event_id=event_id,
            succeeded=success,
            error=error,
        )
        store.create(
            type="notification.test_completed",
            source="product_api",
            payload={
                "channel": payload.channel,
                "result": "success" if success else "failed",
                "error": error or None,
                "correlation_id": event_id,
            },
        )
        return NotificationTestResponse(
            channel=payload.channel,
            success=success,
            state="healthy" if success else "failing",
            attempted_at=attempted_at,
            error=error or None,
        )

    @app.get("/runtime/preflight", response_model=LivePreflightResponse)
    def get_live_preflight() -> LivePreflightResponse:
        status = runner.runtime.get_value("runtime.live_preflight")
        if status is None:
            raise HTTPException(status_code=503, detail="Runtime preflight status unavailable")
        status = dict(status)
        legacy_mode = status.get("execution_mode")
        if legacy_mode == "dry_run":
            status["execution_mode"] = "simulation"
        elif legacy_mode == "real":
            status["execution_mode"] = "live_armed" if status.get("armed") else "live_safe"
        status.setdefault("exchange_enabled", status["execution_mode"] != "simulation")
        status.setdefault("order_submission_enabled", bool(status.get("armed")))
        status.setdefault(
            "credential_environment",
            "none" if status["execution_mode"] == "simulation"
            else "demo" if status["execution_mode"] == "demo"
            else "production",
        )
        status.setdefault("applicable_checks", [])
        return LivePreflightResponse(**status)

    @app.get("/runtime/lease", response_model=RuntimeLeaseResponse)
    def get_runtime_lease() -> RuntimeLeaseResponse:
        status = runner.runtime.get_value("runtime.lease")
        if status is None:
            raise HTTPException(status_code=503, detail="Runtime lease status unavailable")
        return RuntimeLeaseResponse(**status)

    @app.get("/runtime/capabilities", response_model=RuntimeCapabilitiesResponse)
    def get_runtime_capabilities() -> RuntimeCapabilitiesResponse:
        execution_leader = runtime_role == "combined"
        return RuntimeCapabilitiesResponse(
            role=runtime_role,
            execution_leader=execution_leader,
            product_mutations_available=execution_leader,
            runtime_controls_available=execution_leader,
            live_runtime_snapshots_available=execution_leader,
            horizontal_read_replica=not execution_leader,
            authentication_required=bool(selected_api_token),
            constraints=(
                [
                    "SQLite replicas require the same host or a supported shared filesystem",
                    "mutations and live runtime routes must target the execution leader",
                    "multi-host replicas require a future shared transactional database",
                ]
                if not execution_leader
                else [
                    "exactly one combined execution leader may own an account and database",
                    "SQLite is not a multi-host shared database",
                ]
            ),
        )

    @app.get("/risk/limits", response_model=AccountRiskLimitsResponse)
    def get_account_risk_limits() -> AccountRiskLimitsResponse:
        limits = AccountRiskStore().get()
        if limits is None:
            raise HTTPException(status_code=404, detail="Account risk limits are not configured")
        return AccountRiskLimitsResponse(**limits.to_dict())

    @app.put("/risk/limits", response_model=AccountRiskLimitsResponse)
    def put_account_risk_limits(
        payload: AccountRiskLimitsUpdate,
    ) -> AccountRiskLimitsResponse:
        store = AccountRiskStore()
        audit_store = AuditEventStore(store.db_path)
        strategy_store = StrategyStore(store.db_path)
        with ENTRY_EXECUTION_LOCK, store.transaction() as connection:
            before = store.get(connection=connection)
            if before is not None and payload.expected_updated_at != before.updated_at:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Account risk limits changed since they were loaded",
                        "current_updated_at": before.updated_at,
                    },
                )
            if before is not None and before.entries_enabled:
                raise HTTPException(
                    status_code=409,
                    detail="Disable strategy entries before changing account risk limits",
                )
            allowed_instruments = set(payload.allowed_instruments)
            conflicting_strategies = [
                {
                    "strategy_id": strategy.id,
                    "strategy_name": strategy.name,
                    "instruments": sorted(
                        set(strategy.target_instruments) - allowed_instruments
                    ),
                }
                for strategy in strategy_store.list(enabled=True)
                if set(strategy.target_instruments) - allowed_instruments
            ]
            if conflicting_strategies:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "Disable or retarget enabled strategies before removing "
                            "their instruments from the account risk allowlist"
                        ),
                        "strategies": conflicting_strategies,
                    },
                )
            saved = store.save(
                AccountRiskLimits(
                    enabled=payload.enabled,
                    max_order_notional_usd=Decimal(str(payload.max_order_notional_usd)),
                    max_total_exposure_usd=Decimal(str(payload.max_total_exposure_usd)),
                    max_leverage=Decimal(str(payload.max_leverage)),
                    allowed_instruments=tuple(payload.allowed_instruments),
                ),
                connection=connection,
            )
            _record_definition_audit(
                audit_store,
                event_type="risk.limits_updated",
                payload={
                    "before": before.to_dict() if before else None,
                    "after": saved.to_dict(),
                    "result": "updated",
                },
                connection=connection,
            )
        return AccountRiskLimitsResponse(**saved.to_dict())

    @app.get("/instruments", response_model=InstrumentMetadataListResponse)
    def list_instruments() -> InstrumentMetadataListResponse:
        store = InstrumentMetadataStore()
        records = store.list(inst_type="SWAP")
        if not records:
            raise HTTPException(
                status_code=503,
                detail="OKX instrument metadata cache is empty; refresh is required",
            )
        cache = store.cache_status(inst_type="SWAP")
        return InstrumentMetadataListResponse(
            items=[InstrumentMetadataResponse(**record.to_dict()) for record in records],
            **cache,
        )

    @app.post("/instruments/refresh", response_model=InstrumentMetadataListResponse)
    def refresh_instruments() -> InstrumentMetadataListResponse:
        try:
            records = InstrumentMetadataStore().replace_type(
                "SWAP",
                exchange_client().get_instruments(inst_type="SWAP"),
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"OKX instrument metadata refresh failed: {exc}",
            ) from exc
        cache = InstrumentMetadataStore().cache_status(inst_type="SWAP")
        return InstrumentMetadataListResponse(
            items=[InstrumentMetadataResponse(**record.to_dict()) for record in records],
            **cache,
        )

    @app.post(
        "/instruments/{inst_id}/size-quote",
        response_model=InstrumentSizeQuoteResponse,
    )
    def quote_instrument_size(
        inst_id: str,
        payload: InstrumentSizeQuoteRequest,
    ) -> InstrumentSizeQuoteResponse:
        metadata = InstrumentMetadataStore().get(inst_id)
        if metadata is None:
            raise HTTPException(
                status_code=404,
                detail=f"Cached OKX metadata for {inst_id} is unavailable",
            )
        if InstrumentMetadataStore().cache_status(inst_type="SWAP")["stale"]:
            raise HTTPException(
                status_code=409,
                detail="Cached OKX instrument metadata is stale; refresh is required",
            )
        try:
            quote = InstrumentSizer(metadata).quote(
                display_quantity=payload.display_quantity,
                entry_price=payload.entry_price,
                side=payload.side,
                rule_price=payload.rule_price,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return InstrumentSizeQuoteResponse(**quote.to_dict())

    @app.post(
        "/instruments/{inst_id}/contract-quote",
        response_model=InstrumentSizeQuoteResponse,
    )
    def quote_instrument_contracts(
        inst_id: str,
        payload: InstrumentContractQuoteRequest,
    ) -> InstrumentSizeQuoteResponse:
        metadata = InstrumentMetadataStore().get(inst_id)
        if metadata is None:
            raise HTTPException(
                status_code=404,
                detail=f"Cached OKX metadata for {inst_id} is unavailable",
            )
        if InstrumentMetadataStore().cache_status(inst_type="SWAP")["stale"]:
            raise HTTPException(
                status_code=409,
                detail="Cached OKX instrument metadata is stale; refresh is required",
            )
        try:
            quote = InstrumentSizer(metadata).quote_contracts(
                api_quantity_contracts=payload.api_quantity_contracts,
                entry_price=payload.entry_price,
                side=payload.side,
                rule_price=payload.rule_price,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return InstrumentSizeQuoteResponse(**quote.to_dict())

    @app.post(
        "/instruments/{inst_id}/risk-quote",
        response_model=InstrumentRiskQuoteResponse,
    )
    def quote_instrument_risk(
        inst_id: str,
        payload: InstrumentRiskQuoteRequest,
    ) -> InstrumentRiskQuoteResponse:
        quote = build_risk_quote(inst_id, payload)
        return InstrumentRiskQuoteResponse(**quote.to_dict())

    @app.get("/risk/entries", response_model=EntryControlResponse)
    def get_entry_control() -> EntryControlResponse:
        return EntryControlResponse(**EntryControlManager().status().to_dict())

    @app.post("/risk/entries/enable", response_model=EntryControlResponse)
    def enable_entries(payload: EntryControlCommand) -> EntryControlResponse:
        try:
            result = EntryControlManager().enable_entries()
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return EntryControlResponse(**result.to_dict())

    @app.post("/risk/entries/kill", response_model=EntryControlResponse)
    def kill_entries(payload: EntryControlCommand) -> EntryControlResponse:
        result = EntryControlManager().kill_entries()
        if not result.persisted:
            raise HTTPException(status_code=503, detail=result.to_dict())
        return EntryControlResponse(**result.to_dict())

    @app.get("/services", response_model=dict[str, ServiceStatusResponse])
    def list_services() -> dict:
        return {
            name: _serialize_status(runner.get_service_status(name))
            for name in runner.services
        }

    @app.get("/services/{name}", response_model=ServiceStatusResponse)
    def get_service(name: str) -> dict:
        status = runner.get_service_status(name)
        if status is None:
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")
        return _serialize_status(status)

    @app.post("/services/{name}/enable", response_model=ServiceStatusResponse)
    def enable_service(name: str) -> dict:
        if not runner.enable_service(name):
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")
        return _serialize_status(runner.get_service_status(name))

    @app.post("/services/{name}/disable", response_model=ServiceStatusResponse)
    def disable_service(name: str) -> dict:
        if not runner.disable_service(name):
            raise HTTPException(status_code=404, detail=f"Unknown service: {name}")
        return _serialize_status(runner.get_service_status(name))

    @app.get("/events", response_model=list[RuntimeEventResponse])
    def recent_events(limit: int = 100, event_type: Optional[str] = None) -> list[dict]:
        events = runner.runtime.events.recent(limit=limit, event_type=event_type)
        return [_serialize_event(event) for event in events]

    @app.get("/audit/events", response_model=list[AuditEventResponse])
    def list_audit_events(
        limit: int = Query(default=100, ge=1, le=1000),
        event_type: Optional[str] = None,
        source: Optional[str] = None,
        strategy_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        position_id: Optional[str] = None,
        trade_id: Optional[str] = None,
        before: Optional[str] = None,
    ) -> list[AuditEventResponse]:
        store = AuditEventStore()
        return [
            _audit_event_response(event)
            for event in store.list(
                limit=limit,
                event_type=event_type,
                source=source,
                strategy_id=strategy_id,
                correlation_id=correlation_id,
                position_id=position_id,
                trade_id=trade_id,
                before=before,
            )
        ]

    @app.get("/market/btc-regime", response_model=BTCRegimeResponse)
    def get_btc_regime() -> dict:
        regime = runner.runtime.get_value("market.btc_regime")
        if regime is None:
            raise HTTPException(status_code=404, detail="BTC regime is not available yet")
        return regime

    @app.get("/market/candles", response_model=MarketCandlesResponse)
    def get_market_candles(
        inst_id: str = Query(min_length=1, max_length=64),
        bar: str = Query(default="1m", min_length=1, max_length=8),
        limit: int = Query(default=120, ge=2, le=300),
    ) -> MarketCandlesResponse:
        return MarketCandlesResponse(
            inst_id=inst_id,
            bar=bar,
            candles=_fetch_candle_rows(inst_id, bar=bar, limit=limit, client=market_client()),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    @app.get(
        "/market/analysis/support-resistance",
        response_model=SupportResistanceAnalysisResponse,
    )
    def get_support_resistance_analysis(
        inst_id: str = Query(min_length=1, max_length=64),
        bar: str = Query(default="15m", min_length=1, max_length=8),
        limit: int = Query(default=200, ge=20, le=300),
    ) -> SupportResistanceAnalysisResponse:
        return SupportResistanceAnalysisResponse.model_validate(
            support_resistance.analyze(
                inst_id,
                bar=bar,
                limit=limit,
                btc_regime=runner.runtime.get_value("market.btc_regime"),
            )
        )

    @app.get("/strategy/decisions", response_model=list[StrategyDecisionResponse])
    def get_strategy_decisions() -> list[dict]:
        decisions = runner.runtime.get_value("strategy.decisions")
        if decisions is None:
            return []
        return decisions

    @app.get("/position/intents", response_model=list[PositionIntentResponse])
    def get_position_intents() -> list[dict]:
        intents = runner.runtime.get_value("position.intents")
        if intents is None:
            return []
        return intents

    @app.get("/signals/templates", response_model=list[SignalTemplateResponse])
    def list_signal_templates() -> list[dict]:
        return SignalExpressionEngine().templates()

    @app.get("/signals/context", response_model=SignalRuntimeContextResponse)
    def get_signal_runtime_context(
        include_candles: bool = False,
        symbols: str | None = Query(default=None, description="Comma-separated OKX instrument IDs."),
        bar: str | None = None,
        candle_limit: int = Query(default=120, ge=2, le=300),
    ) -> dict:
        context = _signal_runtime_context(runner)
        if include_candles:
            candle_context = _signal_candle_context(
                symbols=_parse_symbols_param(symbols),
                bar=bar,
                limit=candle_limit,
                client=market_client(),
            )
            context = _merge_signal_context(candle_context, context)
        return context

    @app.post("/signals/validate", response_model=SignalValidationResponse)
    def validate_signal(payload: SignalValidationRequest) -> dict:
        return SignalExpressionEngine().validate(payload.expression).to_dict()

    @app.post("/signals/evaluate", response_model=SignalEvaluationResponse)
    def evaluate_signal(payload: SignalEvaluationRequest) -> dict:
        context = payload.context
        if payload.use_candle_context:
            context = _merge_signal_context(
                _signal_candle_context(
                    expression=payload.expression,
                    symbols=payload.symbols,
                    bar=payload.bar,
                    limit=payload.candle_limit,
                    client=market_client(),
                ),
                context,
            )
        if payload.use_runtime_context:
            context = _merge_signal_context(_signal_runtime_context(runner), context)
        return SignalExpressionEngine().evaluate(
            payload.expression,
            context=context,
        ).to_dict()

    @app.get("/account/snapshot", response_model=AccountSnapshotResponse)
    def get_account_snapshot() -> dict:
        snapshot = runner.runtime.get_value("account.snapshot")
        if snapshot is None:
            return {"summary": {}, "positions": [], "orders": []}
        return snapshot

    @app.get(
        "/execution/fills/status",
        response_model=ExecutionFillIngestionStatusResponse,
    )
    def get_execution_fill_status() -> ExecutionFillIngestionStatusResponse:
        status = runner.runtime.get_value("execution.fills.status") or {}
        return ExecutionFillIngestionStatusResponse(**status)

    @app.get("/account/positions", response_model=list[dict])
    def get_account_positions() -> list[dict]:
        snapshot = runner.runtime.get_value("account.snapshot")
        if snapshot is None:
            return []
        return snapshot.get("positions", [])

    @app.get("/account/orders", response_model=list[dict])
    def get_account_orders() -> list[dict]:
        snapshot = runner.runtime.get_value("account.snapshot")
        if snapshot is None:
            return []
        return snapshot.get("orders", [])

    @app.websocket("/ws/events")
    async def websocket_events(websocket: WebSocket, types: Optional[str] = None):
        """Subscribe to live runtime events.

        Optionally filter by event types (comma-separated).
        """
        if selected_api_token:
            credential = websocket.query_params.get("token", "")
            if not secrets.compare_digest(credential, selected_api_token):
                await websocket.close(
                    code=1008,
                    reason="Valid bearer authentication is required",
                )
                return
        if runtime_role == "replica":
            await websocket.close(
                code=1013,
                reason="Live events are available only from the execution leader",
            )
            return
        await websocket.accept()
        event_types = set(types.split(",")) if types else None

        queue = asyncio.Queue()

        def _handler(event: RuntimeEvent):
            if event_types and event.type not in event_types:
                return
            queue.put_nowait(event)

        unsubscribe = runner.runtime.events.subscribe(_handler)

        try:
            while True:
                # Flush existing
                while not queue.empty():
                    try:
                        event = queue.get_nowait()
                        await websocket.send_json(_serialize_event(event))
                    except QueueEmpty:
                        break

                await asyncio.sleep(0.1)

        except WebSocketDisconnect:
            pass
        finally:
            unsubscribe()

    # ------------------------------------------------------------------------
    # Trades & Rules API
    # ------------------------------------------------------------------------

    @app.get("/trades/open", response_model=list[TradeDetailResponse])
    def get_open_trades():
        store = TradeStore()
        trades = store.get_open_trades()
        result = []
        for t in trades:
            rules = store.get_trade_rules(t.id)
            active_rules = [
                TradeRuleResponse(group=_rule_group_response(group), enabled=enabled)
                for group, enabled in rules
            ]
            tr_dict = t.to_dict()
            tr_dict["active_rules"] = active_rules
            result.append(TradeDetailResponse(**tr_dict))
        return result

    @app.get("/trades/history", response_model=list[TradeResponse])
    def get_trade_history(limit: int = 50, strategy_id: Optional[str] = None):
        store = TradeStore()
        trades = store.get_trade_history(limit=limit, strategy_id=strategy_id, status="closed")
        return [TradeResponse(**t.to_dict()) for t in trades]

    @app.post("/trades/{trade_id}/rules", response_model=RuleGroupResponse)
    def add_trade_rule(trade_id: str, payload: TradeRuleAttach):
        store = TradeStore()
        trade = store.get_trade(trade_id)
        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade.status != "open":
            raise HTTPException(status_code=400, detail="Cannot add rules to closed trade")

        # Convert payload to RuleGroup model
        rules = []
        for r_in in payload.rule_group.rules:
            rules.append(
                PositionRule(
                    target=r_in.target,
                    metric=r_in.metric,
                    operator=r_in.operator,
                    value=r_in.value
                )
            )
        
        group = RuleGroup(
            name=payload.rule_group.name,
            operator=payload.rule_group.operator,
            rules=rules
        )
        
        store.attach_rule_group(trade_id, group, enabled=payload.enabled)
        
        return _rule_group_response(group)

    @app.delete("/trades/{trade_id}/rules/{group_id}")
    def remove_trade_rule(trade_id: str, group_id: str):
        store = TradeStore()
        if not store.get_trade(trade_id):
            raise HTTPException(status_code=404, detail="Trade not found")
            
        success = store.remove_trade_rule_group(trade_id, group_id)
        if not success:
            raise HTTPException(status_code=404, detail="Rule group not found for trade")
            
        return {"status": "ok"}

    # ------------------------------------------------------------------------
    # Product-facing strategy and logical position API
    # ------------------------------------------------------------------------

    @app.get("/strategies", response_model=list[StrategySummaryResponse])
    def list_strategies() -> list[StrategySummaryResponse]:
        store = StrategyStore()
        return [_strategy_summary(runner, strategy, store) for strategy in store.list()]

    @app.post("/strategies", response_model=StrategySummaryResponse, status_code=201)
    def create_strategy(payload: StrategyCreate) -> StrategySummaryResponse:
        store = StrategyStore()
        audit_store = AuditEventStore(store.db_path)
        with ENTRY_EXECUTION_LOCK, store.transaction() as connection:
            if payload.id and store.get(payload.id) is not None:
                raise HTTPException(status_code=409, detail="Strategy id already exists")
            strategy = store.create(
                id=payload.id,
                name=payload.name,
                kind=payload.kind,
                enabled=False,
                target_instruments=payload.target_instruments,
                entry_signal=payload.entry_signal,
                default_rules=payload.default_rules,
                metadata=payload.metadata,
                execution_delay_seconds=payload.execution_delay_seconds,
            )
            if payload.enabled:
                validation_errors = _strategy_validation_errors(strategy, store)
                if validation_errors:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "message": "Strategy is not executable",
                            "errors": validation_errors,
                        },
                    )
                strategy = store.update(strategy.id, enabled=True) or strategy
            _record_definition_audit(
                audit_store,
                event_type="strategy.created",
                payload={
                    "strategy_id": strategy.id,
                    "after": _strategy_definition_payload(strategy),
                },
                connection=connection,
            )
        return _strategy_summary(runner, strategy, store)

    @app.post(
        "/strategies/{strategy_id}/risk-stop",
        response_model=StrategySummaryResponse,
    )
    def promote_strategy_risk_stop(
        strategy_id: str,
        payload: StrategyRiskStopPromotionCommand,
    ) -> StrategySummaryResponse:
        require_promotable_research(payload)
        quote = build_risk_quote(payload.inst_id, payload)
        store = StrategyStore()
        audit_store = AuditEventStore(store.db_path)
        with ENTRY_EXECUTION_LOCK, store.transaction() as connection:
            previous = store.get(strategy_id)
            if previous is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            if previous.updated_at != payload.expected_updated_at:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Strategy changed since risk stop review",
                        "current_updated_at": previous.updated_at,
                    },
                )
            if previous.target_instruments != [payload.inst_id]:
                raise HTTPException(
                    status_code=409,
                    detail="Risk stop promotion requires exactly one matching strategy instrument",
                )
            conditions = previous.default_rules.get("close_conditions")
            retained = [
                item for item in conditions or []
                if isinstance(item, dict) and item.get("purpose") != "stop_loss"
            ]
            quote_payload = quote.to_dict()
            retained.insert(0, {
                "purpose": "stop_loss",
                "enabled": True,
                "expression": quote.stop_expression,
                "metadata": {
                    "evidence": {
                        **quote.evidence,
                        "promotion_target": "strategy_default",
                        "risk_quote": quote_payload,
                    },
                },
            })
            defaults = {
                **previous.default_rules,
                "close_conditions": retained,
            }
            strategy = store.update(
                strategy_id,
                default_rules=defaults,
                enabled=False if previous.enabled else None,
            )
            if strategy is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            _record_definition_audit(
                audit_store,
                event_type="strategy.risk_stop_promoted",
                payload={
                    "strategy_id": strategy_id,
                    "inst_id": payload.inst_id,
                    "before": _strategy_definition_payload(previous),
                    "after": _strategy_definition_payload(strategy),
                    "risk_quote": quote_payload,
                    "strategy_auto_disabled": previous.enabled,
                },
                connection=connection,
            )
        return _strategy_summary(runner, strategy, store)

    @app.get(
        "/strategies/{strategy_id}/decisions",
        response_model=list[StrategyDecisionResponse],
    )
    def list_persisted_strategy_decisions(
        strategy_id: str,
        limit: int = Query(default=100, ge=1, le=1000),
        allowed: Optional[bool] = None,
        execution_status: Optional[str] = None,
        before: Optional[str] = None,
    ) -> list[StrategyDecisionResponse]:
        strategy_store = StrategyStore()
        if strategy_store.get(strategy_id) is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        events = AuditEventStore().list_strategy_decisions(
            strategy_id=strategy_id,
            limit=limit,
            allowed=allowed,
            execution_status=execution_status,
            before=before,
        )
        return [_strategy_decision_response(event) for event in events]

    @app.get("/strategies/{strategy_id}", response_model=StrategySummaryResponse)
    def get_strategy(strategy_id: str) -> StrategySummaryResponse:
        store = StrategyStore()
        strategy = store.get(strategy_id)
        if strategy is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        return _strategy_summary(runner, strategy, store)

    @app.patch("/strategies/{strategy_id}", response_model=StrategySummaryResponse)
    def update_strategy(strategy_id: str, payload: StrategyUpdate) -> StrategySummaryResponse:
        store = StrategyStore()
        audit_store = AuditEventStore(store.db_path)
        with ENTRY_EXECUTION_LOCK, store.transaction() as connection:
            previous = store.get(strategy_id)
            if previous is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            if payload.expected_updated_at != previous.updated_at:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Strategy changed since it was loaded",
                        "current_updated_at": previous.updated_at,
                    },
                )
            strategy = store.update(
                strategy_id,
                name=payload.name,
                kind=payload.kind,
                enabled=False if payload.enabled is True else payload.enabled,
                target_instruments=payload.target_instruments,
                entry_signal=payload.entry_signal,
                default_rules=payload.default_rules,
                metadata=payload.metadata,
                execution_delay_seconds=payload.execution_delay_seconds,
            )
            if strategy is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            if strategy.enabled or payload.enabled is True:
                validation_errors = _strategy_validation_errors(strategy, store)
                if validation_errors:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "message": "Strategy is not executable",
                            "errors": validation_errors,
                        },
                    )
                if payload.enabled is True:
                    strategy = store.update(strategy_id, enabled=True) or strategy
            _record_definition_audit(
                audit_store,
                event_type="strategy.updated",
                payload={
                    "strategy_id": strategy.id,
                    "before": _strategy_definition_payload(previous),
                    "after": _strategy_definition_payload(strategy),
                },
                connection=connection,
            )
        return _strategy_summary(runner, strategy, store)

    @app.post("/strategies/{strategy_id}/enable", response_model=StrategySummaryResponse)
    def enable_strategy(
        strategy_id: str,
        payload: StrategyEnableCommand,
    ) -> StrategySummaryResponse:
        store = StrategyStore()
        audit_store = AuditEventStore(store.db_path)
        with ENTRY_EXECUTION_LOCK, store.transaction() as connection:
            strategy = store.get(strategy_id)
            if strategy is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            if payload.expected_updated_at != strategy.updated_at:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Strategy changed since enable was confirmed",
                        "current_updated_at": strategy.updated_at,
                    },
                )
            validation_errors = _strategy_validation_errors(strategy, store)
            if validation_errors:
                raise HTTPException(
                    status_code=400,
                    detail={"message": "Strategy is not executable", "errors": validation_errors},
                )
            strategy = store.update(strategy_id, enabled=True)
            if strategy is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            _record_definition_audit(
                audit_store,
                event_type="strategy.enabled",
                payload={"strategy_id": strategy.id, "enabled": True},
                connection=connection,
            )
        return _strategy_summary(runner, strategy, store)

    @app.post("/strategies/{strategy_id}/disable", response_model=StrategySummaryResponse)
    def disable_strategy(strategy_id: str) -> StrategySummaryResponse:
        store = StrategyStore()
        audit_store = AuditEventStore(store.db_path)
        with store.transaction() as connection:
            previous = store.get(strategy_id)
            strategy = store.update(strategy_id, enabled=False)
            if strategy is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            _record_definition_audit(
                audit_store,
                event_type="strategy.disabled",
                payload={
                    "strategy_id": strategy.id,
                    "was_enabled": bool(previous and previous.enabled),
                    "enabled": False,
                },
                connection=connection,
            )
        return _strategy_summary(runner, strategy, store)

    @app.delete(
        "/strategies/{strategy_id}",
        response_model=MutationStatusResponse,
    )
    def delete_strategy(
        strategy_id: str,
        payload: StrategyDeleteCommand,
    ) -> MutationStatusResponse:
        store = StrategyStore()
        LogicalPositionStore(store.db_path)
        TradeStore(store.db_path)
        audit_store = AuditEventStore(store.db_path)
        with store.transaction() as connection:
            strategy = store.get(strategy_id)
            if strategy is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            if payload.expected_updated_at != strategy.updated_at:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Strategy changed since deletion was confirmed",
                        "current_updated_at": strategy.updated_at,
                    },
                )
            if strategy.enabled:
                raise HTTPException(status_code=409, detail="Disable the strategy before deleting it")
            if store.has_position_history(strategy_id):
                raise HTTPException(
                    status_code=409,
                    detail="Strategy is referenced by position or trade history",
                )
            before = _strategy_definition_payload(strategy)
            if not store.delete(strategy_id):
                raise HTTPException(status_code=404, detail="Strategy not found")
            _record_definition_audit(
                audit_store,
                event_type="strategy.deleted",
                payload={"strategy_id": strategy_id, "before": before},
                connection=connection,
            )
        return MutationStatusResponse(status="deleted", id=strategy_id)

    @app.get(
        "/strategies/{strategy_id}/signals",
        response_model=list[SignalExpressionResponse],
    )
    def list_strategy_signals(strategy_id: str) -> list[SignalExpressionResponse]:
        store = StrategyStore()
        if store.get(strategy_id) is None:
            raise HTTPException(status_code=404, detail="Strategy not found")
        return [
            _signal_expression_response(expression)
            for expression in store.list_signal_expressions(strategy_id)
        ]

    @app.post(
        "/strategies/{strategy_id}/signals",
        response_model=SignalExpressionResponse,
        status_code=201,
    )
    def create_strategy_signal(
        strategy_id: str,
        payload: SignalExpressionCreateCommand,
    ) -> SignalExpressionResponse:
        store = StrategyStore()
        audit_store = AuditEventStore(store.db_path)
        validation = SignalExpressionEngine().validate(payload.expression)
        if not validation.valid:
            raise HTTPException(
                status_code=400,
                detail={"message": "Signal expression validation failed", "errors": validation.errors},
            )
        with store.transaction() as connection:
            strategy = store.get(strategy_id)
            if strategy is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            if payload.expected_strategy_updated_at != strategy.updated_at:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Strategy changed since child signal creation was confirmed",
                        "current_updated_at": strategy.updated_at,
                    },
                )
            expression = store.create_signal_expression(
                strategy_id=strategy_id,
                purpose=payload.purpose,
                expression=payload.expression,
            )
            if expression is None:
                raise HTTPException(status_code=404, detail="Strategy not found")
            store.update(strategy_id)
            _record_definition_audit(
                audit_store,
                event_type="signal_expression.created",
                payload={
                    "strategy_id": strategy_id,
                    "signal_expression_id": expression.id,
                    "after": _signal_expression_payload(expression),
                },
                connection=connection,
            )
        return _signal_expression_response(expression)

    @app.get(
        "/strategies/{strategy_id}/signals/{expression_id}",
        response_model=SignalExpressionResponse,
    )
    def get_strategy_signal(
        strategy_id: str,
        expression_id: str,
    ) -> SignalExpressionResponse:
        expression = StrategyStore().get_signal_expression(strategy_id, expression_id)
        if expression is None:
            raise HTTPException(status_code=404, detail="Signal expression not found")
        return _signal_expression_response(expression)

    @app.patch(
        "/strategies/{strategy_id}/signals/{expression_id}",
        response_model=SignalExpressionResponse,
    )
    def update_strategy_signal(
        strategy_id: str,
        expression_id: str,
        payload: SignalExpressionUpdate,
    ) -> SignalExpressionResponse:
        store = StrategyStore()
        audit_store = AuditEventStore(store.db_path)
        if payload.expression is not None:
            _validate_signal_or_400(payload.expression)
        with store.transaction() as connection:
            previous = store.get_signal_expression(strategy_id, expression_id)
            if previous is None:
                raise HTTPException(status_code=404, detail="Signal expression not found")
            if payload.expected_updated_at != previous.updated_at:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Signal expression changed since it was loaded",
                        "current_updated_at": previous.updated_at,
                    },
                )
            expression = store.update_signal_expression(
                strategy_id,
                expression_id,
                purpose=payload.purpose,
                expression=payload.expression,
            )
            if expression is None:
                raise HTTPException(status_code=404, detail="Signal expression not found")
            store.update(strategy_id)
            strategy = store.get(strategy_id)
            auto_disabled = False
            if strategy is not None and strategy.enabled:
                errors = _strategy_validation_errors(strategy, store)
                if errors:
                    store.update(strategy_id, enabled=False)
                    auto_disabled = True
            _record_definition_audit(
                audit_store,
                event_type="signal_expression.updated",
                payload={
                    "strategy_id": strategy_id,
                    "signal_expression_id": expression_id,
                    "before": _signal_expression_payload(previous),
                    "after": _signal_expression_payload(expression),
                    "strategy_auto_disabled": auto_disabled,
                },
                connection=connection,
            )
        return _signal_expression_response(expression)

    @app.delete(
        "/strategies/{strategy_id}/signals/{expression_id}",
        response_model=MutationStatusResponse,
    )
    def delete_strategy_signal(
        strategy_id: str,
        expression_id: str,
        payload: SignalExpressionDeleteCommand,
    ) -> MutationStatusResponse:
        store = StrategyStore()
        audit_store = AuditEventStore(store.db_path)
        with store.transaction() as connection:
            expression = store.get_signal_expression(strategy_id, expression_id)
            if expression is None:
                raise HTTPException(status_code=404, detail="Signal expression not found")
            if payload.expected_updated_at != expression.updated_at:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Signal expression changed since deletion was confirmed",
                        "current_updated_at": expression.updated_at,
                    },
                )
            if not store.delete_signal_expression(strategy_id, expression_id):
                raise HTTPException(status_code=404, detail="Signal expression not found")
            store.update(strategy_id)
            strategy = store.get(strategy_id)
            auto_disabled = False
            if strategy is not None and strategy.enabled:
                errors = _strategy_validation_errors(strategy, store)
                if errors:
                    store.update(strategy_id, enabled=False)
                    auto_disabled = True
            _record_definition_audit(
                audit_store,
                event_type="signal_expression.deleted",
                payload={
                    "strategy_id": strategy_id,
                    "signal_expression_id": expression_id,
                    "before": _signal_expression_payload(expression),
                    "strategy_auto_disabled": auto_disabled,
                },
                connection=connection,
            )
        return MutationStatusResponse(status="deleted", id=expression_id)

    @app.get("/positions/groups", response_model=list[PositionGroupResponse])
    def list_position_groups(
        group_by: Literal["instrument_side", "strategy", "exchange_position"] = Query(
            default="instrument_side",
        ),
        status: Literal[
            "active",
            "all",
            "pending_open",
            "open",
            "reducing",
            "closing",
            "closed",
            "failed",
        ] = Query(default="active"),
        limit: int = Query(default=500, ge=1, le=2000),
    ) -> list[PositionGroupResponse]:
        store = LogicalPositionStore()
        positions = (
            store.list_active()
            if status == "active"
            else store.list(status=status, limit=None)
        )
        grouped: dict[str, list[LogicalPositionRecord]] = {}
        for position in positions:
            if group_by == "strategy":
                key = (
                    f"strategy:{position.strategy_id or 'unassigned'}:"
                    f"{position.inst_id}:{position.side}"
                )
            elif group_by == "exchange_position":
                exchange_key = position.exchange_position_key or f"{position.inst_id}:{position.side}"
                key = f"exchange:{exchange_key}"
            else:
                key = f"instrument:{position.inst_id}:{position.side}"
            grouped.setdefault(key, []).append(position)

        responses: list[PositionGroupResponse] = []
        active_statuses = {"pending_open", "open", "reducing", "closing"}
        for key, members in grouped.items():
            statuses: dict[str, int] = {}
            opened_quantity = 0.0
            remaining_quantity = 0.0
            weighted_entry_total = 0.0
            weighted_entry_quantity = 0.0
            for member in members:
                statuses[member.status] = statuses.get(member.status, 0) + 1
                opened_quantity += member.opened_quantity or 0.0
                remaining = member.remaining_quantity or 0.0
                remaining_quantity += remaining
                if remaining > 0 and member.entry_price > 0:
                    weighted_entry_total += member.entry_price * remaining
                    weighted_entry_quantity += remaining
            first = members[0]
            responses.append(
                PositionGroupResponse(
                    key=key,
                    group_by=group_by,
                    inst_id=first.inst_id,
                    side=first.side,
                    strategy_id=first.strategy_id or None if group_by == "strategy" else None,
                    exchange_position_key=(
                        first.exchange_position_key or f"{first.inst_id}:{first.side}"
                        if group_by == "exchange_position"
                        else None
                    ),
                    position_ids=[member.id for member in members],
                    position_count=len(members),
                    active_count=sum(member.status in active_statuses for member in members),
                    opened_quantity=opened_quantity,
                    remaining_quantity=remaining_quantity,
                    weighted_entry_price=(
                        weighted_entry_total / weighted_entry_quantity
                        if weighted_entry_quantity
                        else None
                    ),
                    statuses=statuses,
                )
            )
        return sorted(responses, key=lambda item: item.key)[:limit]

    @app.post(
        "/positions/manual-open",
        response_model=LogicalPositionUnitResponse,
        status_code=201,
    )
    def manual_open_position(
        payload: ManualPositionOpenRequest,
    ) -> LogicalPositionUnitResponse:
        preflight = runner.runtime.get_value("runtime.live_preflight")
        if not isinstance(preflight, dict):
            raise HTTPException(
                status_code=503,
                detail="Runtime execution mode is unavailable; manual open is blocked",
            )
        if preflight.get("execution_mode") not in {"simulation", "dry_run"}:
            detail = (
                "Manual live open requires an armed runtime and enabled entry gate"
                if not preflight.get("armed") or not entry_order_placement_enabled()
                else "Manual exchange open remains disabled in this build; use simulation"
            )
            raise HTTPException(status_code=409, detail=detail)

        metadata_store = InstrumentMetadataStore()
        metadata = metadata_store.get(payload.inst_id)
        if metadata is None:
            raise HTTPException(
                status_code=404,
                detail=f"Cached OKX metadata for {payload.inst_id} is unavailable",
            )
        if metadata_store.cache_status(inst_type="SWAP")["stale"]:
            raise HTTPException(
                status_code=409,
                detail="Cached OKX instrument metadata is stale; refresh is required",
            )
        try:
            quote = InstrumentSizer(metadata).quote(
                display_quantity=payload.display_quantity,
                entry_price=payload.entry_price,
                side=payload.side,
            )
            entry_price = Decimal(payload.entry_price)
            stop_loss = Decimal(payload.stop_loss_price)
            take_profit = (
                Decimal(payload.take_profit_price)
                if payload.take_profit_price is not None
                else None
            )
            if payload.side == "long" and stop_loss >= entry_price:
                raise ValueError("Long stop loss must be below entry price")
            if payload.side == "short" and stop_loss <= entry_price:
                raise ValueError("Short stop loss must be above entry price")
            if take_profit is not None and (
                (payload.side == "long" and take_profit <= entry_price)
                or (payload.side == "short" and take_profit >= entry_price)
            ):
                raise ValueError("Take profit must be on the profitable side of entry")
        except (ValueError, ArithmeticError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        trade_store = TradeStore()
        position_store = LogicalPositionStore(trade_store.db_path)
        audit_store = AuditEventStore(trade_store.db_path)
        position_id = uuid4().hex[:12]
        position = LogicalPositionRecord(
            id=position_id,
            source="manual",
            inst_id=metadata.inst_id,
            side=payload.side,
            opened_quantity=0,
            remaining_quantity=0,
            entry_price=float(entry_price),
            status="pending_open",
            client_order_id=f"dry{position_id}",
            metadata_json=json.dumps(
                {
                    "execution_status": "simulated",
                    "confirmation_source": "dry_run",
                    "operator_display_quantity": quote.to_dict()["display_quantity"],
                    "operator_display_currency": quote.display_currency,
                    "api_quantity_contracts": quote.to_dict()["api_quantity_contracts"],
                    "estimated_notional_usdt": quote.to_dict()["estimated_notional_usdt"],
                    "order_action": "open",
                    "dry_run": True,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        with ENTRY_EXECUTION_LOCK:
            with position_store.transaction() as connection:
                position_store.save(position)
                position_store.create_close_condition(
                    position_id=position.id,
                    purpose="stop_loss",
                    expression={
                        "type": "price_below" if payload.side == "long" else "price_above",
                        "symbol": metadata.inst_id,
                        "value": float(stop_loss),
                    },
                    enabled=True,
                    metadata={"source": "manual_open"},
                )
                if take_profit is not None:
                    position_store.create_close_condition(
                        position_id=position.id,
                        purpose="take_profit",
                        expression={
                            "type": "price_above" if payload.side == "long" else "price_below",
                            "symbol": metadata.inst_id,
                            "value": float(take_profit),
                        },
                        enabled=True,
                        metadata={"source": "manual_open"},
                    )
                position = position_store.record_allocation(
                    LogicalPositionAllocation(
                        id=f"dry-open-{position.id}",
                        position_id=position.id,
                        action="open",
                        quantity=float(quote.api_quantity_contracts),
                        price=float(entry_price),
                        exchange_order_id=f"dry-open-{position.id}",
                        reason="confirmed manual dry-run open",
                        metadata_json=json.dumps({"source": "manual", "dry_run": True}),
                    )
                ) or position
                _record_definition_audit(
                    audit_store,
                    event_type="position.manual_open_simulated",
                    payload={
                        "position_id": position.id,
                        "source": "manual",
                        "instrument": metadata.inst_id,
                        "side": payload.side,
                        "size_quote": quote.to_dict(),
                    },
                    connection=connection,
                )
        return _logical_position_response(
            store=trade_store,
            position_store=position_store,
            position=position_store.get(position.id) or position,
            account_snapshot={},
            intents=[],
            audit_events=[],
        )

    @app.get("/positions/logical", response_model=list[LogicalPositionUnitResponse])
    def list_logical_positions(
        status: str = "open",
        strategy_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[LogicalPositionUnitResponse]:
        store = TradeStore()
        position_store = LogicalPositionStore(store.db_path)
        if runtime_role == "combined":
            _backfill_logical_positions(
                trade_store=store,
                position_store=position_store,
                status=status,
                strategy_id=strategy_id,
                limit=limit,
            )
        positions = position_store.list(status=status, strategy_id=strategy_id, limit=limit)

        account_snapshot = runner.runtime.get_value("account.snapshot") or {}
        intents = runner.runtime.get_value("position.intents") or []
        events = runner.runtime.events.recent(limit=limit)
        reconciliations: dict[str, PositionReconciliation] = {}
        if runtime_role == "combined":
            reconciliations = PositionReconciler().reconcile(
                logical_positions=positions,
                exchange_positions=account_snapshot.get("positions", []),
            )
            for position_id, reconciliation in reconciliations.items():
                position_store.update_reconciliation(
                    position_id,
                    exchange_position_key=reconciliation.exchange_position_key,
                    reconciliation=reconciliation.to_dict(),
                )
            positions = position_store.list(status=status, strategy_id=strategy_id, limit=limit)
        return [
            _logical_position_response(
                store=store,
                position_store=position_store,
                position=position,
                account_snapshot=account_snapshot,
                intents=intents,
                audit_events=events,
                reconciliation=reconciliations.get(position.id),
            )
            for position in positions
        ]

    @app.get(
        "/positions/logical/{position_id}/chart",
        response_model=LogicalPositionChartResponse,
    )
    def get_logical_position_chart(
        position_id: str,
        bar: str = Query(default="1m", min_length=1, max_length=8),
        limit: int = Query(default=120, ge=2, le=300),
    ) -> LogicalPositionChartResponse:
        trade_store = TradeStore()
        position_store = LogicalPositionStore(trade_store.db_path)
        position = (
            _get_or_backfill_logical_position(
                trade_store=trade_store,
                position_store=position_store,
                position_id=position_id,
            )
            if runtime_role == "combined"
            else position_store.get(position_id)
        )
        if position is None:
            raise HTTPException(status_code=404, detail="Logical position not found")
        candles = _fetch_candle_rows(
            position.inst_id, bar=bar, limit=limit, client=market_client()
        )
        return LogicalPositionChartResponse(
            position_id=position.id,
            inst_id=position.inst_id,
            bar=bar,
            candles=candles,
            overlays=_position_chart_overlays(position_store, position, candles),
            fetched_at=datetime.now(timezone.utc).isoformat(),
        )

    @app.get(
        "/account/exposure-reconciliation",
        response_model=AccountExposureReconciliationResponse,
    )
    def get_account_exposure_reconciliation() -> AccountExposureReconciliationResponse:
        position_store = LogicalPositionStore()
        report = PositionReconciler().reconcile_account(
            logical_positions=position_store.list_active(),
            exchange_positions=exchange_client().get_positions(inst_type="SWAP"),
        )
        return AccountExposureReconciliationResponse(**report.to_dict())

    @app.post(
        "/positions/import",
        response_model=LogicalPositionUnitResponse,
        status_code=201,
    )
    def import_external_position(
        payload: ExternalPositionImportRequest,
    ) -> LogicalPositionUnitResponse:
        trade_store = TradeStore()
        position_store = LogicalPositionStore(trade_store.db_path)
        try:
            position = PositionImportService(exchange_client(require_orders=True), position_store).import_unexplained(
                PositionImportRequest(
                    inst_id=payload.inst_id,
                    side=payload.side,
                    close_conditions=[item.model_dump() for item in payload.close_conditions],
                    reason=payload.reason,
                )
            )
        except PositionImportConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PositionProtectionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _logical_position_response(
            store=trade_store,
            position_store=position_store,
            position=position,
            account_snapshot={"positions": []},
            intents=[],
            audit_events=[],
        )

    @app.post(
        "/positions/logical/{position_id}/protection",
        response_model=LogicalPositionUnitResponse,
    )
    def attach_logical_position_protection(
        position_id: str,
        payload: PositionProtectionCommand,
    ) -> LogicalPositionUnitResponse:
        del payload
        trade_store = TradeStore()
        position_store = LogicalPositionStore(trade_store.db_path)
        try:
            position = PositionProtectionService(
                exchange_client(require_orders=True), position_store
            ).protect(position_id)
        except PositionProtectionError as exc:
            status_code = 404 if str(exc) == "logical position not found" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return _logical_position_response(
            store=trade_store,
            position_store=position_store,
            position=position,
            account_snapshot=runner.runtime.get_value("account.snapshot") or {},
            intents=runner.runtime.get_value("position.intents") or [],
            audit_events=runner.runtime.events.recent(limit=100),
        )

    @app.post(
        "/positions/logical/{position_id}/adopt-recovery",
        response_model=LogicalPositionUnitResponse,
    )
    def adopt_recovered_logical_position(
        position_id: str,
        payload: PositionRecoveryAdoptionCommand,
    ) -> LogicalPositionUnitResponse:
        trade_store = TradeStore()
        position_store = LogicalPositionStore(trade_store.db_path)
        try:
            position = PositionProtectionService(
                exchange_client(require_orders=True),
                position_store,
                AuditEventStore(trade_store.db_path),
            ).adopt_recovered_position(
                position_id,
                stop_loss=Decimal(str(payload.stop_loss)),
                reason=payload.reason,
            )
        except PositionProtectionError as exc:
            status_code = 404 if str(exc) == "logical position not found" else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return _logical_position_response(
            store=trade_store,
            position_store=position_store,
            position=position,
            account_snapshot=runner.runtime.get_value("account.snapshot") or {},
            intents=runner.runtime.get_value("position.intents") or [],
            audit_events=runner.runtime.events.recent(limit=100),
        )

    @app.post(
        "/positions/logical/{position_id}/risk-stop",
        response_model=LogicalPositionUnitResponse,
    )
    def promote_logical_position_risk_stop(
        position_id: str,
        payload: PositionRiskStopPromotionCommand,
    ) -> LogicalPositionUnitResponse:
        require_promotable_research(payload)
        trade_store = TradeStore()
        position_store = LogicalPositionStore(trade_store.db_path)
        position = position_store.get(position_id)
        if position is None:
            raise HTTPException(status_code=404, detail="Logical position not found")
        if position.updated_at != payload.expected_position_updated_at:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Logical position changed since risk stop review",
                    "current_updated_at": position.updated_at,
                },
            )
        quote = build_risk_quote(position.inst_id, payload)
        if payload.side != position.side or quote.size.entry_price != Decimal(str(position.entry_price)):
            raise HTTPException(
                status_code=409,
                detail="Risk stop review must use the position's persisted side and entry price",
            )
        remaining = Decimal(str(position.remaining_quantity or position.opened_quantity or 0))
        if Decimal(str(quote.size.api_quantity_contracts)) != remaining:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Risk quote size does not equal the existing logical position quantity; "
                    "existing exposure cannot be silently resized"
                ),
            )
        enabled_stops = [
            item for item in position_store.list_close_conditions(position.id, enabled=True)
            if item.purpose == "stop_loss"
        ]
        condition = (
            position_store.get_close_condition(position.id, payload.condition_id)
            if payload.condition_id
            else enabled_stops[0] if len(enabled_stops) == 1 else None
        )
        if len(enabled_stops) > 1:
            raise HTTPException(status_code=409, detail="Position has multiple enabled stop rules")
        if condition is not None and (
            payload.expected_condition_updated_at is None
            or condition.updated_at != payload.expected_condition_updated_at
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "Stop condition changed since risk stop review",
                    "current_updated_at": condition.updated_at,
                },
            )
        quote_payload = quote.to_dict()
        promoted_metadata = {
            **(condition.metadata if condition is not None else {}),
            "evidence": {
                **quote.evidence,
                "promotion_target": "logical_position_override",
                "risk_quote": quote_payload,
            },
        }
        protection = position_store.get_protection(position.id)
        if protection is not None:
            if condition is None:
                raise HTTPException(status_code=409, detail="Owned protection has no stop condition")
            try:
                position = PositionProtectionService(
                    exchange_client(require_orders=True),
                    position_store,
                    AuditEventStore(trade_store.db_path),
                ).amend_stop_condition(
                    position.id,
                    condition.id,
                    expression=quote.stop_expression,
                    reason=payload.reason,
                    condition_metadata=promoted_metadata,
                    intent_metadata={
                        "operation": "risk_stop_promotion",
                        "risk_quote": quote_payload,
                    },
                    expected_position_updated_at=payload.expected_position_updated_at,
                    expected_condition_updated_at=payload.expected_condition_updated_at,
                )
            except PositionProtectionError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        else:
            audit_store = AuditEventStore(trade_store.db_path)
            with ENTRY_EXECUTION_LOCK, position_store.transaction() as connection:
                current = position_store.get(position.id)
                if current is None:
                    raise HTTPException(status_code=404, detail="Logical position not found")
                if current.updated_at != payload.expected_position_updated_at:
                    raise HTTPException(status_code=409, detail="Logical position changed since review")
                if condition is None:
                    condition = position_store.create_close_condition(
                        position_id=position.id,
                        purpose="stop_loss",
                        expression=quote.stop_expression,
                        enabled=True,
                        metadata=promoted_metadata,
                    )
                else:
                    current_condition = position_store.get_close_condition(position.id, condition.id)
                    if current_condition is None or current_condition.updated_at != payload.expected_condition_updated_at:
                        raise HTTPException(status_code=409, detail="Stop condition changed since review")
                    condition = position_store.update_close_condition(
                        position.id,
                        condition.id,
                        expression=quote.stop_expression,
                        enabled=True,
                        metadata=promoted_metadata,
                    )
                if condition is None:
                    raise HTTPException(status_code=409, detail="Risk stop could not be persisted")
                _record_definition_audit(
                    audit_store,
                    event_type="position.risk_stop_promoted",
                    payload={
                        "position_id": position.id,
                        "strategy_id": position.strategy_id,
                        "condition_id": condition.id,
                        "risk_quote": quote_payload,
                    },
                    connection=connection,
                )
                position = current
        return _logical_position_response(
            store=trade_store,
            position_store=position_store,
            position=position,
            account_snapshot=runner.runtime.get_value("account.snapshot") or {},
            intents=runner.runtime.get_value("position.intents") or [],
            audit_events=runner.runtime.events.recent(limit=100),
        )

    @app.post(
        "/positions/logical/{position_id}/protection/stop",
        response_model=LogicalPositionUnitResponse,
    )
    def amend_logical_position_stop(
        position_id: str,
        payload: PositionStopAmendCommand,
    ) -> LogicalPositionUnitResponse:
        trade_store = TradeStore()
        position_store = LogicalPositionStore(trade_store.db_path)
        _validate_signal_or_400(payload.expression)
        try:
            position = PositionProtectionService(
                exchange_client(require_orders=True),
                position_store,
                AuditEventStore(trade_store.db_path),
            ).amend_stop_condition(
                position_id,
                payload.condition_id,
                expression=payload.expression,
                reason=payload.reason,
                expected_position_updated_at=payload.expected_position_updated_at,
                expected_condition_updated_at=payload.expected_condition_updated_at,
            )
        except PositionProtectionError as exc:
            protection = position_store.get_protection(position_id)
            if str(exc) in {"logical position not found", "close condition not found"}:
                status_code = 404
            elif protection is not None and protection.status == "failed":
                status_code = 502
            else:
                status_code = 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return _logical_position_response(
            store=trade_store,
            position_store=position_store,
            position=position,
            account_snapshot=runner.runtime.get_value("account.snapshot") or {},
            intents=runner.runtime.get_value("position.intents") or [],
            audit_events=runner.runtime.events.recent(limit=100),
        )

    @app.post(
        "/positions/logical/{position_id}/break-even",
        response_model=LogicalPositionUnitResponse,
    )
    def move_logical_position_to_break_even(
        position_id: str,
        payload: PositionBreakEvenCommand,
    ) -> LogicalPositionUnitResponse:
        trade_store = TradeStore()
        position_store = LogicalPositionStore(trade_store.db_path)
        try:
            position = PositionProtectionService(
                exchange_client(require_orders=True),
                position_store,
                AuditEventStore(trade_store.db_path),
            ).move_to_break_even(
                position_id,
                payload.condition_id,
                lock_in_pct=Decimal(str(payload.lock_in_pct)),
                reason=payload.reason,
                expected_position_updated_at=payload.expected_position_updated_at,
                expected_condition_updated_at=payload.expected_condition_updated_at,
                entry_fee_rate=Decimal(str(payload.entry_fee_rate)),
                exit_fee_rate=Decimal(str(payload.exit_fee_rate)),
                slippage_rate=Decimal(str(payload.slippage_rate)),
            )
        except PositionProtectionError as exc:
            protection = position_store.get_protection(position_id)
            if str(exc) in {"logical position not found", "close condition not found"}:
                status_code = 404
            elif protection is not None and protection.status == "failed":
                status_code = 502
            else:
                status_code = 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
        return _logical_position_response(
            store=trade_store,
            position_store=position_store,
            position=position,
            account_snapshot=runner.runtime.get_value("account.snapshot") or {},
            intents=runner.runtime.get_value("position.intents") or [],
            audit_events=runner.runtime.events.recent(limit=100),
        )

    @app.get("/positions/logical/{position_id}", response_model=LogicalPositionUnitResponse)
    def get_logical_position(position_id: str) -> LogicalPositionUnitResponse:
        store = TradeStore()
        position_store = LogicalPositionStore(store.db_path)
        position = (
            _get_or_backfill_logical_position(
                trade_store=store,
                position_store=position_store,
                position_id=position_id,
            )
            if runtime_role == "combined"
            else position_store.get(position_id)
        )
        if position is None:
            raise HTTPException(status_code=404, detail="Logical position not found")
        account_snapshot = runner.runtime.get_value("account.snapshot") or {}
        intents = runner.runtime.get_value("position.intents") or []
        events = runner.runtime.events.recent(limit=100)
        reconciliation = None
        if runtime_role == "combined":
            reconciliations = PositionReconciler().reconcile(
                logical_positions=[position],
                exchange_positions=account_snapshot.get("positions", []),
            )
            reconciliation = reconciliations.get(position.id)
        if runtime_role == "combined" and reconciliation is not None:
            position_store.update_reconciliation(
                position.id,
                exchange_position_key=reconciliation.exchange_position_key,
                reconciliation=reconciliation.to_dict(),
            )
            refreshed = position_store.get(position.id)
            if refreshed is not None:
                position = refreshed
        return _logical_position_response(
            store=store,
            position_store=position_store,
            position=position,
            account_snapshot=account_snapshot,
            intents=intents,
            audit_events=events,
            reconciliation=reconciliation,
        )

    @app.get(
        "/positions/logical/{position_id}/allocations",
        response_model=list[LogicalPositionAllocationResponse],
    )
    def list_logical_position_allocations(
        position_id: str,
    ) -> list[LogicalPositionAllocationResponse]:
        position_store = LogicalPositionStore()
        if position_store.get(position_id) is None:
            raise HTTPException(status_code=404, detail="Logical position not found")
        return [
            _allocation_response(allocation)
            for allocation in position_store.list_allocations(position_id)
        ]

    @app.post(
        "/positions/logical/{position_id}/allocations",
        response_model=ConfirmedPositionFillResponse,
        status_code=201,
    )
    def record_confirmed_position_fill(
        position_id: str,
        payload: ConfirmedPositionFillCreate,
    ) -> ConfirmedPositionFillResponse:
        trade_store = TradeStore()
        position_store = LogicalPositionStore(trade_store.db_path)
        service = ExecutionAllocationService(
            trade_store=trade_store,
            position_store=position_store,
            audit_store=AuditEventStore(trade_store.db_path),
        )
        try:
            result = service.ingest(
                ConfirmedExecutionFill(
                    fill_id=payload.fill_id,
                    position_id=position_id,
                    action=payload.action,
                    quantity=payload.quantity,
                    price=payload.price,
                    fee=payload.fee,
                    exchange_order_id=payload.exchange_order_id,
                    correlation_id=payload.correlation_id,
                    confirmation_source=payload.confirmation_source,
                    occurred_at=payload.occurred_at or "",
                    reason=payload.reason,
                    metadata=payload.metadata,
                )
            )
        except AllocationConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return ConfirmedPositionFillResponse(
            allocation=_allocation_response(result.allocation),
            idempotent=result.idempotent,
            position_id=result.position.id,
            trade_id=result.position.trade_id,
            status=result.position.status,
            opened_quantity=result.position.opened_quantity,
            remaining_quantity=result.position.remaining_quantity,
            average_entry_price=result.position.entry_price,
            execution_status=result.execution_status,
        )

    @app.post(
        "/positions/logical/{position_id}/close",
        response_model=LogicalPositionCloseResponse,
    )
    def close_logical_position(
        position_id: str,
        payload: LogicalPositionCloseRequest,
    ) -> LogicalPositionCloseResponse:
        manager = runner.services.get("position_manager")
        if manager is None or not hasattr(manager, "request_close"):
            raise HTTPException(
                status_code=503,
                detail="Position manager service is not available",
            )
        try:
            result = manager.request_close(position_id, reason=payload.reason)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return LogicalPositionCloseResponse(**result)

    @app.post(
        "/positions/logical/{position_id}/reduce",
        response_model=LogicalPositionReduceResponse,
    )
    def reduce_logical_position(
        position_id: str,
        payload: LogicalPositionReduceRequest,
    ) -> LogicalPositionReduceResponse:
        manager = runner.services.get("position_manager")
        if manager is None or not hasattr(manager, "request_reduce"):
            raise HTTPException(
                status_code=503,
                detail="Position manager service is not available",
            )
        try:
            result = manager.request_reduce(
                position_id,
                quantity=payload.quantity,
                reason=payload.reason,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return LogicalPositionReduceResponse(**result)

    @app.get(
        "/positions/logical/{position_id}/close-conditions",
        response_model=list[LogicalPositionCloseConditionResponse],
    )
    def list_logical_position_close_conditions(
        position_id: str,
        enabled: Optional[bool] = None,
    ) -> list[LogicalPositionCloseConditionResponse]:
        store = TradeStore()
        position_store = LogicalPositionStore(store.db_path)
        position = (
            _get_or_backfill_logical_position(
                trade_store=store,
                position_store=position_store,
                position_id=position_id,
            )
            if runtime_role == "combined"
            else position_store.get(position_id)
        )
        if position is None:
            raise HTTPException(status_code=404, detail="Logical position not found")
        return [
            _close_condition_response(condition)
            for condition in position_store.list_close_conditions(position.id, enabled=enabled)
        ]

    @app.post(
        "/positions/logical/{position_id}/close-conditions",
        response_model=LogicalPositionCloseConditionResponse,
        status_code=201,
    )
    def create_logical_position_close_condition(
        position_id: str,
        payload: LogicalPositionCloseConditionCreateCommand,
    ) -> LogicalPositionCloseConditionResponse:
        store = TradeStore()
        position_store = LogicalPositionStore(store.db_path)
        position = _get_or_backfill_logical_position(
            trade_store=store,
            position_store=position_store,
            position_id=position_id,
        )
        if position is None:
            raise HTTPException(status_code=404, detail="Logical position not found")
        _validate_signal_or_400(payload.expression)
        audit_store = AuditEventStore(store.db_path)
        with ENTRY_EXECUTION_LOCK:
            with position_store.transaction() as connection:
                current_position = position_store.get(position.id)
                if current_position is None:
                    raise HTTPException(status_code=404, detail="Logical position not found")
                if payload.expected_position_updated_at != current_position.updated_at:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "Logical position changed since rule creation was confirmed",
                            "current_updated_at": current_position.updated_at,
                        },
                    )
                position = current_position
                if _protected_stop_mutation_blocked(
                    position_store,
                    position,
                    purpose=payload.purpose,
                    enabled=payload.enabled,
                ):
                    _raise_protected_stop_edit_conflict()
                condition = position_store.create_close_condition(
                    position_id=position.id,
                    purpose=payload.purpose,
                    expression=payload.expression,
                    enabled=payload.enabled,
                    metadata=payload.metadata,
                )
                if condition is None:
                    raise HTTPException(status_code=404, detail="Logical position not found")
                if payload.purpose == "stop_loss" and payload.enabled:
                    _invalidate_external_position_protection(position_store, position)
                _record_definition_audit(
                    audit_store,
                    event_type="position_close_condition.created",
                    payload={
                        "position_id": position.id,
                        "strategy_id": position.strategy_id,
                        "condition_id": condition.id,
                        "after": _close_condition_response(condition).model_dump(),
                    },
                    connection=connection,
                )
        return _close_condition_response(condition)

    @app.patch(
        "/positions/logical/{position_id}/close-conditions/{condition_id}",
        response_model=LogicalPositionCloseConditionResponse,
    )
    def update_logical_position_close_condition(
        position_id: str,
        condition_id: str,
        payload: LogicalPositionCloseConditionUpdate,
    ) -> LogicalPositionCloseConditionResponse:
        store = TradeStore()
        position_store = LogicalPositionStore(store.db_path)
        position = _get_or_backfill_logical_position(
            trade_store=store,
            position_store=position_store,
            position_id=position_id,
        )
        if position is None:
            raise HTTPException(status_code=404, detail="Logical position not found")
        if payload.expression is not None:
            _validate_signal_or_400(payload.expression)
        audit_store = AuditEventStore(store.db_path)
        with ENTRY_EXECUTION_LOCK:
            with position_store.transaction() as connection:
                existing = position_store.get_close_condition(position.id, condition_id)
                if existing is None:
                    raise HTTPException(status_code=404, detail="Close condition not found")
                if payload.expected_updated_at != existing.updated_at:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "Close condition changed since it was loaded",
                            "current_updated_at": existing.updated_at,
                        },
                    )
                definition_changed = any(
                    value is not None
                    for value in (payload.purpose, payload.expression, payload.enabled)
                )
                if _protected_stop_mutation_blocked(
                    position_store,
                    position,
                    condition=existing,
                    purpose=payload.purpose,
                    enabled=payload.enabled,
                    definition_changed=definition_changed,
                ):
                    _raise_protected_stop_edit_conflict()
                condition = position_store.update_close_condition(
                    position.id,
                    condition_id,
                    purpose=payload.purpose,
                    expression=payload.expression,
                    enabled=payload.enabled,
                    metadata=payload.metadata,
                )
                if condition is None:
                    raise HTTPException(status_code=404, detail="Close condition not found")
                next_purpose = (
                    existing.purpose if payload.purpose is None else payload.purpose
                )
                next_enabled = (
                    existing.enabled if payload.enabled is None else payload.enabled
                )
                if definition_changed and (
                    (existing.purpose == "stop_loss" and existing.enabled)
                    or (next_purpose == "stop_loss" and next_enabled)
                ):
                    _invalidate_external_position_protection(position_store, position)
                _record_definition_audit(
                    audit_store,
                    event_type="position_close_condition.updated",
                    payload={
                        "position_id": position.id,
                        "strategy_id": position.strategy_id,
                        "condition_id": condition.id,
                        "before": _close_condition_response(existing).model_dump(),
                        "after": _close_condition_response(condition).model_dump(),
                    },
                    connection=connection,
                )
        return _close_condition_response(condition)

    @app.delete("/positions/logical/{position_id}/close-conditions/{condition_id}")
    def delete_logical_position_close_condition(
        position_id: str,
        condition_id: str,
        payload: LogicalPositionCloseConditionDeleteCommand,
    ) -> dict:
        store = TradeStore()
        position_store = LogicalPositionStore(store.db_path)
        position = _get_or_backfill_logical_position(
            trade_store=store,
            position_store=position_store,
            position_id=position_id,
        )
        if position is None:
            raise HTTPException(status_code=404, detail="Logical position not found")
        audit_store = AuditEventStore(store.db_path)
        with ENTRY_EXECUTION_LOCK:
            with position_store.transaction() as connection:
                existing = position_store.get_close_condition(position.id, condition_id)
                if existing is None:
                    raise HTTPException(status_code=404, detail="Close condition not found")
                if payload.expected_updated_at != existing.updated_at:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "message": "Close condition changed since deletion was confirmed",
                            "current_updated_at": existing.updated_at,
                        },
                    )
                if _protected_stop_mutation_blocked(
                    position_store,
                    position,
                    condition=existing,
                ):
                    _raise_protected_stop_edit_conflict()
                if not position_store.delete_close_condition(position.id, condition_id):
                    raise HTTPException(status_code=404, detail="Close condition not found")
                if existing.purpose == "stop_loss" and existing.enabled:
                    _invalidate_external_position_protection(position_store, position)
                _record_definition_audit(
                    audit_store,
                    event_type="position_close_condition.deleted",
                    payload={
                        "position_id": position.id,
                        "strategy_id": position.strategy_id,
                        "condition_id": condition_id,
                        "before": _close_condition_response(existing).model_dump(),
                    },
                    connection=connection,
                )
        return {"status": "ok"}

    return app
