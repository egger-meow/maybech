"""Position manager daemon evaluates logical-position close conditions.

For each open logical position unit, this service reads current runtime prices,
builds signal evaluation context, evaluates first-class close conditions, and
falls back to legacy trade rule groups for compatibility. Armed live triggers
submit reduce-only closes and wait for confirmed fill allocation.
"""

from __future__ import annotations

import json
import time
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from src.data.candles import CandleManager
from src.daemon.service import DaemonService
from src.exchange.client import OKXClient, disable_entry_order_placement
from src.trading.account_risk import AccountRiskStore
from src.trading.audit_event_store import AuditEventStore
from src.trading.executor import Executor
from src.trading.logical_position_store import (
    LogicalPositionAllocation,
    LogicalPositionCloseCondition,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.position_protection import (
    PositionProtectionError,
    PositionProtectionService,
)
from src.trading.position_rule_model import calculate_break_even_target
from src.trading.rules import RuleGroup
from src.trading.signal_context import (
    build_signal_context_from_candles,
    collect_signal_requirements,
)
from src.trading.signal_engine import SignalEvaluationResult, SignalExpressionEngine
from src.trading.trade_store import TradeRecord, TradeStore
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class PositionManagerService(DaemonService):
    """Actively monitors logical positions and closes them when conditions fire."""

    name = "position_manager"
    interval = 5.0

    def __init__(
        self,
        store: TradeStore,
        *,
        dry_run: bool = True,
        candle_manager: CandleManager | None = None,
        enable_candle_context: bool = True,
        candle_bar: str | None = None,
        candle_limit: int = 120,
        audit_store: AuditEventStore | None = None,
        close_executor: Executor | None = None,
        protection_service: PositionProtectionService | None = None,
    ) -> None:
        super().__init__()
        self.store = store
        self.dry_run = dry_run
        self.candle_manager = candle_manager
        self.enable_candle_context = enable_candle_context
        self.candle_bar = candle_bar or "1m"
        self.candle_limit = candle_limit
        self.audit_store = audit_store or AuditEventStore(store.db_path)
        self.close_executor = close_executor
        self.protection_service = protection_service
        self._price_history: dict[str, deque[tuple[float, float]]] = {}
        self._max_history_seconds = 600

    def setup(self) -> None:
        if self.close_executor is None:
            self.close_executor = Executor(OKXClient(), dry_run=self.dry_run)
        if (
            not self.dry_run
            and self.protection_service is None
            and hasattr(self.close_executor, "client")
        ):
            self.protection_service = PositionProtectionService(
                self.close_executor.client,
                LogicalPositionStore(self.store.db_path),
            )
        logger.info("PositionManagerService setup complete. Dry run: %s", self.dry_run)

    def tick(self) -> None:
        if self.runtime is None:
            return

        retrying_positions = (
            self._retry_unknown_closes() if not self.dry_run else set()
        )

        prices = self._gather_prices()
        if not prices:
            return

        self._update_price_history(prices)
        velocities = self._compute_velocities()
        btc_price = prices.get("BTC-USDT-SWAP", 0.0)
        signal_context = self._build_signal_context(prices=prices, velocities=velocities)

        position_store = LogicalPositionStore(self.store.db_path)
        open_trades = self.store.get_open_trades()
        trades_by_id = {trade.id: trade for trade in open_trades}
        for trade in open_trades:
            position_store.ensure_from_trade(trade)

        open_positions = position_store.list(status="open", limit=500)
        if not open_positions:
            return

        signal_context = self._merge_signal_context(
            self._build_candle_signal_context(
                position_store=position_store,
                positions=open_positions,
            ),
            signal_context,
        )

        intents: list[dict] = []
        for position in open_positions:
            if position.id in retrying_positions:
                intents.append(
                    {
                        "position_id": position.id,
                        "trade_id": position.trade_id,
                        "inst_id": position.inst_id,
                        "side": position.side,
                        "action": "hold",
                        "reason": "close submission recovery completed this tick",
                    }
                )
                continue
            trade = trades_by_id.get(position.trade_id or position.id)
            current_price = prices.get(position.inst_id, 0.0)
            if current_price <= 0:
                intents.append({
                    "position_id": position.id,
                    "trade_id": position.trade_id,
                    "inst_id": position.inst_id,
                    "side": position.side,
                    "action": "hold",
                    "reason": "no price data",
                })
                continue

            pnl_pct = self._pnl_pct(position, current_price)
            condition, evaluation = self._triggered_close_condition(
                position_store=position_store,
                position=position,
                signal_context=signal_context,
            )
            if condition is not None and evaluation is not None:
                rule_action = condition.metadata.get("rule_definition", {}).get("action", {})
                action_type = str(rule_action.get("type") or "close_position")
                if action_type == "amend_stop" and condition.purpose == "break_even":
                    intents.append(self._handle_break_even_rule(
                        position_store=position_store,
                        position=position,
                        condition=condition,
                        current_price=current_price,
                    ))
                    continue
                if action_type in {"amend_stop", "require_manual_review"}:
                    try:
                        position_metadata = json.loads(position.metadata_json or "{}")
                    except json.JSONDecodeError:
                        position_metadata = {}
                    if not (
                        position_metadata.get("requires_manual_review") is True
                        and position_metadata.get("rule_action_blocked") == action_type
                        and position_metadata.get("rule_condition_id") == condition.id
                    ):
                        position_store.merge_metadata(position.id, {
                            "requires_manual_review": True,
                            "rule_action_blocked": action_type,
                            "rule_condition_id": condition.id,
                        })
                    intents.append({
                        "position_id": position.id,
                        "trade_id": position.trade_id,
                        "inst_id": position.inst_id,
                        "side": position.side,
                        "action": "manual_review",
                        "reason": f"rule action {action_type} requires its dedicated lifecycle",
                        "condition_id": condition.id,
                        "condition_purpose": condition.purpose,
                        "condition_evidence": evaluation.evidence,
                    })
                    continue
                execution_action = "reduce" if action_type == "reduce_position" else "close"
                requested_quantity = None
                if execution_action == "reduce":
                    quantity_basis = str(rule_action.get("quantity_basis") or "initial")
                    base_quantity = (
                        (position.opened_quantity or 0.0)
                        if quantity_basis == "initial"
                        else (position.remaining_quantity or position.opened_quantity or 0.0)
                    )
                    requested_quantity = (
                        base_quantity * float(rule_action["quantity_fraction"])
                    )
                intents.append(
                    self._handle_close_trigger(
                        position_store=position_store,
                        position=position,
                        trade=trade,
                        current_price=current_price,
                        btc_price=btc_price,
                        exit_reason=f"close_condition_fired:{condition.purpose}:{condition.id}",
                        trigger_payload={
                            "trigger_type": "close_condition",
                            "condition_id": condition.id,
                            "condition_purpose": condition.purpose,
                            "condition_expression": condition.expression,
                            "condition_evidence": evaluation.evidence,
                            "rule_action": rule_action,
                        },
                        execution_action=execution_action,
                        requested_quantity=requested_quantity,
                    )
                )
                continue

            triggered_group = None
            if trade is not None:
                triggered_group = self._triggered_legacy_rule_group(
                    trade=trade,
                    prices=prices,
                    velocities=velocities,
                    pnl_pct=pnl_pct,
                )
            if triggered_group is not None:
                intents.append(
                    self._handle_close_trigger(
                        position_store=position_store,
                        position=position,
                        trade=trade,
                        current_price=current_price,
                        btc_price=btc_price,
                        exit_reason=f"rule_fired:{triggered_group.name}",
                        trigger_payload={
                            "trigger_type": "legacy_trade_rule",
                            "rule_group_id": triggered_group.id,
                            "rule_group_name": triggered_group.name,
                        },
                    )
                )
                continue

            close_conditions = position_store.list_close_conditions(position.id)
            intents.append({
                "position_id": position.id,
                "trade_id": position.trade_id,
                "inst_id": position.inst_id,
                "side": position.side,
                "action": "hold",
                "reason": "no close condition fired",
                "current_price": current_price,
                "entry_price": position.entry_price,
                "pnl_pct": round(pnl_pct, 2),
                "close_conditions": [
                    {"id": item.id, "purpose": item.purpose, "enabled": item.enabled}
                    for item in close_conditions
                ],
                "legacy_trade_rules": self._legacy_rule_info(trade) if trade is not None else [],
                "strategy_id": position.strategy_id,
            })

        self.runtime.set_value("position_manager.intents", intents)
        self.publish_event("position_manager.tick", {
            "open_count": len(open_positions),
            "intents": intents,
        })

    def _handle_break_even_rule(
        self,
        *,
        position_store: LogicalPositionStore,
        position: LogicalPositionRecord,
        condition: LogicalPositionCloseCondition,
        current_price: float,
    ) -> dict[str, Any]:
        definition = condition.metadata.get("rule_definition", {})
        parameters = definition.get("parameters", {})
        stop_conditions = [
            item for item in position_store.list_close_conditions(position.id, enabled=True)
            if item.purpose == "stop_loss"
        ]
        base = {
            "position_id": position.id,
            "trade_id": position.trade_id,
            "inst_id": position.inst_id,
            "side": position.side,
            "condition_id": condition.id,
            "condition_purpose": condition.purpose,
        }
        if len(stop_conditions) != 1:
            return {
                **base,
                "action": "manual_review",
                "reason": "break-even requires exactly one enabled stop loss",
            }
        stop = stop_conditions[0]
        try:
            target, cost_evidence = calculate_break_even_target(
                entry_price=position.entry_price,
                side=position.side,
                entry_fee_rate=parameters.get("entry_fee_rate", "0.0005"),
                exit_fee_rate=parameters.get("exit_fee_rate", "0.0005"),
                slippage_rate=parameters.get("slippage_rate", "0.0005"),
                lock_in_pct=parameters.get("lock_in_pct", "0"),
            )
        except ValueError as exc:
            return {**base, "action": "manual_review", "reason": str(exc)}
        favorable = (
            Decimal(str(current_price)) > target
            if position.side == "long"
            else Decimal(str(current_price)) < target
        )
        existing_state = condition.metadata.get("break_even_state", {})
        armed_at = existing_state.get("armed_at")
        state = {
            "status": "armed",
            "armed_at": armed_at or datetime.now(timezone.utc).isoformat(),
            "target_stop": str(target),
            "current_price": str(current_price),
            "costs": cost_evidence,
        }
        armed = condition
        if existing_state.get("status") != "armed":
            armed = position_store.update_close_condition(
                position.id,
                condition.id,
                metadata={**condition.metadata, "break_even_state": state},
            )
        if armed is None:
            return {**base, "action": "manual_review", "reason": "break-even state disappeared"}
        if not favorable:
            return {
                **base,
                "action": "break_even_armed",
                "reason": "activation fired but cost-adjusted target is not yet favorable",
                "target_stop": float(target),
            }
        if self.dry_run:
            expression = {
                "type": "price_below" if position.side == "long" else "price_above",
                "symbol": position.inst_id,
                "value": float(target),
            }
            position_store.update_close_condition(
                position.id,
                stop.id,
                expression=expression,
                metadata={
                    **stop.metadata,
                    "break_even": {**state, "status": "applied"},
                },
            )
        else:
            if self.protection_service is None:
                return {**base, "action": "manual_review", "reason": "protection service unavailable"}
            try:
                self.protection_service.move_to_break_even(
                    position.id,
                    stop.id,
                    lock_in_pct=Decimal(str(parameters.get("lock_in_pct", "0"))),
                    reason=f"automatic break-even rule {condition.id}",
                    expected_position_updated_at=position.updated_at,
                    expected_condition_updated_at=stop.updated_at,
                    entry_fee_rate=Decimal(str(parameters.get("entry_fee_rate", "0.0005"))),
                    exit_fee_rate=Decimal(str(parameters.get("exit_fee_rate", "0.0005"))),
                    slippage_rate=Decimal(str(parameters.get("slippage_rate", "0.0005"))),
                )
            except PositionProtectionError as exc:
                return {**base, "action": "manual_review", "reason": str(exc)}
        applied_at = datetime.now(timezone.utc).isoformat()
        position_store.update_close_condition(
            position.id,
            condition.id,
            enabled=False,
            metadata={
                **armed.metadata,
                "break_even_state": {
                    **state,
                    "status": "applied",
                    "applied_at": applied_at,
                },
            },
        )
        payload = {
            **base,
            "action": "break_even_applied",
            "target_stop": float(target),
            "costs": cost_evidence,
        }
        self._publish_and_record_event("position.break_even_applied", payload)
        return payload

    def request_close(self, position_id: str, *, reason: str) -> dict[str, Any]:
        """Submit or simulate one operator-confirmed logical-unit close."""
        position_store = LogicalPositionStore(self.store.db_path)
        position = position_store.get(position_id)
        if position is None:
            raise LookupError("Logical position not found")
        if position.status != "open":
            raise ValueError(f"Position is {position.status}, not open")
        trade = self.store.get_trade(position.trade_id) if position.trade_id else None
        prices = self._gather_prices() if self.runtime is not None else {}
        return self._handle_close_trigger(
            position_store=position_store,
            position=position,
            trade=trade,
            current_price=prices.get(position.inst_id, position.entry_price),
            btc_price=prices.get("BTC-USDT-SWAP", 0.0),
            exit_reason=f"operator_requested:{reason}",
            trigger_payload={
                "trigger_type": "operator",
                "operator_reason": reason,
            },
        )

    def request_reduce(
        self,
        position_id: str,
        *,
        quantity: float,
        reason: str,
    ) -> dict[str, Any]:
        """Submit or simulate one operator-confirmed partial logical-unit reduce."""
        position_store = LogicalPositionStore(self.store.db_path)
        position = position_store.get(position_id)
        if position is None:
            raise LookupError("Logical position not found")
        if position.status != "open":
            raise ValueError(f"Position is {position.status}, not open")
        remaining = position.remaining_quantity or position.opened_quantity or 0.0
        if quantity <= 0:
            raise ValueError("Reduce quantity must be positive")
        if quantity >= remaining:
            raise ValueError(
                "Reduce quantity must be less than remaining quantity; use close instead"
            )
        trade = self.store.get_trade(position.trade_id) if position.trade_id else None
        prices = self._gather_prices() if self.runtime is not None else {}
        return self._handle_close_trigger(
            position_store=position_store,
            position=position,
            trade=trade,
            current_price=prices.get(position.inst_id, position.entry_price),
            btc_price=prices.get("BTC-USDT-SWAP", 0.0),
            exit_reason=f"operator_reduce:{reason}",
            trigger_payload={
                "trigger_type": "operator_reduce",
                "operator_reason": reason,
            },
            execution_action="reduce",
            requested_quantity=quantity,
        )

    def _gather_prices(self) -> dict[str, float]:
        prices: dict[str, float] = {}
        btc_regime = self.runtime.get_value("market.btc_regime")
        if btc_regime and "price" in btc_regime:
            prices["BTC-USDT-SWAP"] = float(btc_regime["price"])

        snapshot = self.runtime.get_value("account.snapshot") or {}
        for pos in snapshot.get("positions", []):
            inst_id = pos.get("inst_id", "")
            mark_px = pos.get("mark_price", "")
            if inst_id and mark_px:
                try:
                    prices[inst_id] = float(mark_px)
                except (ValueError, TypeError):
                    pass
        return prices

    def _update_price_history(self, prices: dict[str, float]) -> None:
        now = time.time()
        cutoff = now - self._max_history_seconds
        for inst_id, price in prices.items():
            if inst_id not in self._price_history:
                self._price_history[inst_id] = deque(maxlen=1200)
            hist = self._price_history[inst_id]
            hist.append((now, price))
            while hist and hist[0][0] < cutoff:
                hist.popleft()

    def _compute_velocities(self) -> dict[str, float]:
        velocities: dict[str, float] = {}
        now = time.time()
        windows = {"velocity_1m": 60, "velocity_5m": 300, "velocity_10m": 600}

        for inst_id, hist in self._price_history.items():
            if len(hist) < 2:
                continue
            current = hist[-1][1]
            for name, seconds in windows.items():
                cutoff = now - seconds
                oldest = None
                for ts, px in hist:
                    if ts >= cutoff:
                        oldest = px
                        break
                if oldest and oldest > 0:
                    velocities[f"{inst_id}:{name}"] = ((current - oldest) / oldest) * 100
        return velocities

    def _build_signal_context(
        self,
        *,
        prices: dict[str, float],
        velocities: dict[str, float],
    ) -> dict:
        changes_pct: dict[str, float] = {}
        velocity_windows = {
            "velocity_1m": 60,
            "velocity_5m": 300,
            "velocity_10m": 600,
        }
        for key, value in velocities.items():
            try:
                inst_id, metric = key.rsplit(":", 1)
            except ValueError:
                continue
            window = velocity_windows.get(metric)
            if window is not None:
                changes_pct[f"{inst_id}:{window}"] = value

        return {
            "prices": dict(prices),
            "changes_pct": changes_pct,
            "volume_ratios": {},
            "source": {"runtime": True, "position_manager": True},
        }

    def _build_candle_signal_context(
        self,
        *,
        position_store: LogicalPositionStore,
        positions: list[LogicalPositionRecord],
    ) -> dict:
        if not self.enable_candle_context:
            return {}

        requirements_by_bar: dict[str, dict[str, set]] = {}
        for position in positions:
            for condition in position_store.list_close_conditions(position.id, enabled=True):
                requirements = collect_signal_requirements(condition.expression)
                needs_candles = bool(requirements["windows_seconds"] or requirements["timeframes"])
                if not needs_candles:
                    continue

                bars = requirements["timeframes"] or {self.candle_bar}
                for bar in bars:
                    entry = requirements_by_bar.setdefault(
                        str(bar),
                        {"symbols": set(), "windows_seconds": set()},
                    )
                    entry["symbols"].update(requirements["symbols"])
                    entry["windows_seconds"].update(requirements["windows_seconds"])

        if not requirements_by_bar:
            return {}

        merged: dict[str, Any] = {}
        for bar, requirements in requirements_by_bar.items():
            symbols = sorted(requirements["symbols"])
            if not symbols:
                continue
            try:
                manager = self._candle_manager()
                candles = {
                    symbol: manager.fetch(symbol, bar=bar, limit=self.candle_limit)
                    for symbol in symbols
                }
                context = build_signal_context_from_candles(
                    candles,
                    bar=bar,
                    windows_seconds=set(requirements["windows_seconds"]),
                )
                context.setdefault("source", {}).setdefault("candles", {}).update(
                    {
                        "requested_symbols": symbols,
                        "limit": self.candle_limit,
                        "windows_seconds": sorted(requirements["windows_seconds"]),
                    }
                )
                merged = self._merge_signal_context(merged, context)
            except Exception as exc:
                logger.warning("Unable to build candle signal context for %s: %s", bar, exc)
                self._publish_and_record_event("position.candle_context_error", {
                    "bar": bar,
                    "symbols": symbols,
                    "error": str(exc),
                })
        return merged

    def _candle_manager(self) -> CandleManager:
        if self.candle_manager is None:
            self.candle_manager = CandleManager(OKXClient())
        return self.candle_manager

    def _merge_signal_context(self, base: dict, override: dict) -> dict:
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

    def _triggered_close_condition(
        self,
        *,
        position_store: LogicalPositionStore,
        position: LogicalPositionRecord,
        signal_context: dict,
    ) -> tuple[LogicalPositionCloseCondition | None, SignalEvaluationResult | None]:
        engine = SignalExpressionEngine()
        for condition in position_store.list_close_conditions(position.id, enabled=True):
            evaluation = engine.evaluate(condition.expression, context=signal_context)
            self._record_audit_event(
                "position.close_condition_evaluated",
                {
                    "position_id": position.id,
                    "trade_id": position.trade_id,
                    "inst_id": position.inst_id,
                    "side": position.side,
                    "strategy_id": position.strategy_id,
                    "condition_id": condition.id,
                    "condition_purpose": condition.purpose,
                    "condition_expression": condition.expression,
                    "matched": evaluation.matched,
                    "valid": evaluation.valid,
                    "errors": evaluation.errors,
                    "evidence": evaluation.evidence,
                    "evaluated_at": evaluation.evaluated_at,
                },
            )
            if evaluation.valid and evaluation.matched:
                return condition, evaluation
            if not evaluation.valid:
                self._publish_and_record_event("position.close_condition_invalid", {
                    "position_id": position.id,
                    "trade_id": position.trade_id,
                    "condition_id": condition.id,
                    "errors": evaluation.errors,
                })
        return None, None

    def _triggered_legacy_rule_group(
        self,
        *,
        trade: TradeRecord,
        prices: dict[str, float],
        velocities: dict[str, float],
        pnl_pct: float,
    ) -> RuleGroup | None:
        for group, enabled in self.store.get_trade_rules(trade.id):
            if not enabled:
                continue
            if group.evaluate(
                self_inst_id=trade.inst_id,
                prices=prices,
                velocities=velocities,
                pnl_pct=pnl_pct,
            ):
                return group
        return None

    def _handle_close_trigger(
        self,
        *,
        position_store: LogicalPositionStore,
        position: LogicalPositionRecord,
        trade: TradeRecord | None,
        current_price: float,
        btc_price: float,
        exit_reason: str,
        trigger_payload: dict,
        execution_action: str = "close",
        requested_quantity: float | None = None,
    ) -> dict:
        if execution_action not in {"close", "reduce"}:
            raise ValueError("execution_action must be close or reduce")
        remaining_quantity = (
            position.remaining_quantity or position.opened_quantity or 0.0
        )
        execution_quantity = (
            remaining_quantity if requested_quantity is None else requested_quantity
        )
        if execution_quantity <= 0 or execution_quantity > remaining_quantity:
            raise ValueError("Execution quantity exceeds remaining logical quantity")
        if execution_action == "reduce" and execution_quantity >= remaining_quantity:
            raise ValueError("Reduce quantity must leave positive logical quantity")
        pending_status = "closing" if execution_action == "close" else "reducing"
        event_prefix = f"position.{execution_action}"
        event_payload = {
            "position_id": position.id,
            "trade_id": position.trade_id,
            "inst_id": position.inst_id,
            "side": position.side,
            "current_price": current_price,
            "exit_reason": exit_reason,
            "strategy_id": position.strategy_id,
            "dry_run": self.dry_run,
            "execution_action": execution_action,
            "quantity": execution_quantity,
            **trigger_payload,
        }

        if not self.dry_run and self.close_executor is None:
            logger.error(
                "LIVE %s BLOCKED: %s %s %s reason=%s. "
                "No exchange close-order executor is wired.",
                execution_action.upper(),
                position.id,
                position.side,
                position.inst_id,
                exit_reason,
            )
            self._publish_and_record_event(f"{event_prefix}_blocked", {
                **event_payload,
                "reason": "live close order executor is not implemented",
            })
            return {
                "position_id": position.id,
                "trade_id": position.trade_id,
                "inst_id": position.inst_id,
                "side": position.side,
                "action": f"manual_{execution_action}_required",
                "reason": exit_reason,
                "current_price": current_price,
                "strategy_id": position.strategy_id,
                **trigger_payload,
            }

        if not self.dry_run:
            correlation_id = uuid4().hex
            close_intent = {
                **event_payload,
                "correlation_id": correlation_id,
                "execution_status": "pending_submission",
            }
            if execution_quantity <= 0:
                self._publish_and_record_event(
                    f"{event_prefix}_submission_failed",
                    {**close_intent, "reason": "position has no allocated quantity"},
                )
                return {
                    **close_intent,
                    "action": f"{execution_action}_submission_failed",
                }
            if not self._try_record_audit_event(
                f"{event_prefix}_requested",
                close_intent,
            ):
                self.publish_event(
                    f"{event_prefix}_blocked",
                    {**close_intent, "reason": "pre-submission audit persistence failed"},
                )
                return {
                    **close_intent,
                    "action": f"manual_{execution_action}_required",
                    "reason": "pre-submission audit persistence failed",
                }

            claimed = position_store.claim_pending_execution(
                position.id,
                expected_status="open",
                status=pending_status,
                client_order_id=correlation_id,
                metadata={
                    "correlation_id": correlation_id,
                    "client_order_id": correlation_id,
                    "order_action": execution_action,
                    "execution_reason": exit_reason,
                    "execution_quantity": execution_quantity,
                    "close_reason": exit_reason,
                    "close_quantity": execution_quantity,
                    "previous_exchange_order_id": position.exchange_order_id,
                    "execution_status": "submitting",
                    "rule_condition_id": str(trigger_payload.get("condition_id") or ""),
                    "rule_action": trigger_payload.get("rule_action") or {},
                },
            )
            if claimed is None:
                return {
                    **close_intent,
                    "action": f"{execution_action}_already_pending",
                    "reason": "position was claimed by another exit request",
                }

            protection = position_store.get_protection(position.id)
            protection_canceled = False
            if protection is not None:
                if self.protection_service is None:
                    position_store.release_pending_execution(
                        position.id,
                        correlation_id=correlation_id,
                        restore_status="open",
                    )
                    reason = "protective-stop lifecycle service is unavailable"
                    self._publish_and_record_event(
                        f"{event_prefix}_blocked",
                        {**close_intent, "reason": reason},
                    )
                    return {
                        **close_intent,
                        "action": "manual_review",
                        "reason": reason,
                    }
                try:
                    protection_canceled = self.protection_service.cancel_for_close(
                        position.id,
                        reason=exit_reason,
                    )
                except PositionProtectionError as exc:
                    position_store.release_pending_execution(
                        position.id,
                        correlation_id=correlation_id,
                        restore_status="open",
                    )
                    current_protection = position_store.get_protection(position.id)
                    if current_protection is not None and current_protection.status in {
                        "canceling",
                        "failed",
                    }:
                        self._disable_entries_after_protection_failure()
                    self._publish_and_record_event(
                        f"{event_prefix}_blocked",
                        {**close_intent, "reason": str(exc)},
                    )
                    return {
                        **close_intent,
                        "action": "manual_review",
                        "reason": str(exc),
                    }

            try:
                result = self.close_executor.close_position(
                    inst_id=position.inst_id,
                    position_side=position.side,
                    quantity=execution_quantity,
                    client_order_id=correlation_id,
                    pos_side=self._exchange_position_side(position),
                )
            except Exception as exc:
                if protection_canceled:
                    position_store.merge_metadata(
                        position.id,
                        {
                            "execution_status": f"{execution_action}_submission_unknown",
                            "execution_submission_attempts": 1,
                            "execution_submission_error": str(exc),
                            "close_submission_attempts": 1,
                            "close_submission_error": str(exc),
                        },
                    )
                    self._publish_and_record_event(
                        f"{event_prefix}_submission_unknown",
                        {**close_intent, "error": str(exc)},
                    )
                    return {
                        **close_intent,
                        "action": f"{execution_action}_submission_pending",
                        "reason": "exit acceptance is unknown; retry is scheduled",
                    }
                position_store.release_pending_execution(
                    position.id,
                    correlation_id=correlation_id,
                    restore_status="open",
                )
                restoration_error = self._restore_protection_after_failed_close(
                    position.id,
                    required=protection_canceled,
                )
                self._publish_and_record_event(
                    f"{event_prefix}_submission_failed",
                    {
                        **close_intent,
                        "error": str(exc),
                        "protection_restoration_error": restoration_error,
                    },
                )
                return {
                    **close_intent,
                    "action": (
                        "manual_review"
                        if restoration_error
                        else f"{execution_action}_submission_failed"
                    ),
                    "reason": restoration_error or None,
                }
            order_id = self._extract_order_id(result)
            if not order_id:
                if protection_canceled:
                    position_store.merge_metadata(
                        position.id,
                        {
                            "execution_status": f"{execution_action}_submission_unknown",
                            "execution_submission_attempts": 1,
                            "execution_submission_result": result,
                            "close_submission_attempts": 1,
                            "close_submission_result": result,
                        },
                    )
                    self._publish_and_record_event(
                        f"{event_prefix}_submission_unknown",
                        {**close_intent, "execution_result": result},
                    )
                    return {
                        **close_intent,
                        "action": f"{execution_action}_submission_pending",
                        "reason": "exit acceptance is unknown; retry is scheduled",
                    }
                position_store.release_pending_execution(
                    position.id,
                    correlation_id=correlation_id,
                    restore_status="open",
                )
                restoration_error = self._restore_protection_after_failed_close(
                    position.id,
                    required=protection_canceled,
                )
                self._publish_and_record_event(
                    f"{event_prefix}_submission_failed",
                    {
                        **close_intent,
                        "execution_result": result,
                        "protection_restoration_error": restoration_error,
                    },
                )
                return {
                    **close_intent,
                    "action": (
                        "manual_review"
                        if restoration_error
                        else f"{execution_action}_submission_failed"
                    ),
                    "reason": restoration_error or None,
                }

            updated = position_store.mark_pending_execution(
                position.id,
                status=pending_status,
                exchange_order_id=order_id,
                metadata={
                    "correlation_id": correlation_id,
                    "order_action": execution_action,
                    "execution_order_id": order_id,
                    f"{execution_action}_order_id": order_id,
                    "client_order_id": correlation_id,
                    "execution_reason": exit_reason,
                    "execution_quantity": execution_quantity,
                    "close_reason": exit_reason,
                    "close_quantity": execution_quantity,
                    "execution_status": "submitted",
                },
            )
            if updated is None:
                self._publish_and_record_event(
                    f"{event_prefix}_submission_failed",
                    {
                        **close_intent,
                        "exchange_order_id": order_id,
                        "reason": "position disappeared after order submission",
                    },
                )
                return {**close_intent, "action": "manual_review"}

            submitted_payload = {
                **close_intent,
                "exchange_order_id": order_id,
                "execution_result": result,
                "execution_status": "submitted",
            }
            self._publish_and_record_event(
                f"{event_prefix}_submitted",
                submitted_payload,
            )
            return {
                **submitted_payload,
                "action": f"{execution_action}_submitted",
                "status": updated.status,
            }

        closed_trade = None
        if execution_action == "close" and trade is not None:
            closed_trade = self.store.close_trade(
                trade.id,
                exit_price=current_price,
                exit_reason=exit_reason,
                btc_price_at_exit=btc_price,
            )

        updated_position = position_store.record_allocation(
            LogicalPositionAllocation(
                position_id=position.id,
                action=execution_action,
                quantity=execution_quantity,
                price=current_price,
                reason=exit_reason,
                metadata_json=json.dumps(
                    {
                        "source": "position_manager_dry_run",
                        **trigger_payload,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )
        if updated_position is None:
            updated_position = position_store.update_status(
                position.id,
                status="closed" if execution_action == "close" else "open",
                remaining_quantity=(
                    0.0
                    if execution_action == "close"
                    else remaining_quantity - execution_quantity
                ),
            )
        if execution_action == "reduce":
            condition_id = str(trigger_payload.get("condition_id") or "")
            condition = (
                position_store.get_close_condition(position.id, condition_id)
                if condition_id else None
            )
            if condition is not None:
                position_store.update_close_condition(
                    position.id,
                    condition.id,
                    enabled=False,
                    metadata={
                        **condition.metadata,
                        "execution_state": {
                            "status": "completed",
                            "allocation_action": "reduce",
                            "quantity": execution_quantity,
                        },
                    },
                )

        logger.info(
            "POSITION %s: %s %s %s @ %.4f (reason=%s)",
            execution_action.upper(),
            position.id,
            position.side,
            position.inst_id,
            current_price,
            exit_reason,
        )
        self._publish_and_record_event(f"position.{execution_action}d", {
            **event_payload,
            "entry_price": position.entry_price,
            "exit_price": current_price,
            "pnl": None if closed_trade is None else closed_trade.pnl,
            "pnl_pct": None if closed_trade is None else closed_trade.pnl_pct,
            "remaining_quantity": None if updated_position is None else updated_position.remaining_quantity,
        })
        return {
            "position_id": position.id,
            "trade_id": position.trade_id,
            "inst_id": position.inst_id,
            "side": position.side,
            "action": f"{execution_action}d",
            "reason": exit_reason,
            "current_price": current_price,
            "pnl": None if closed_trade is None else closed_trade.pnl,
            "pnl_pct": None if closed_trade is None else closed_trade.pnl_pct,
            "strategy_id": position.strategy_id,
            **trigger_payload,
        }

    def _retry_unknown_closes(self) -> set[str]:
        touched: set[str] = set()
        if self.close_executor is None:
            return touched
        position_store = LogicalPositionStore(self.store.db_path)
        for position in position_store.list_pending_executions():
            if position.status not in {"closing", "reducing"} or position.exchange_order_id:
                continue
            try:
                metadata = json.loads(position.metadata_json or "{}")
            except json.JSONDecodeError:
                continue
            execution_action = str(metadata.get("order_action") or "close")
            if execution_action not in {"close", "reduce"}:
                continue
            if metadata.get("execution_status") != f"{execution_action}_submission_unknown":
                continue
            touched.add(position.id)
            client_order_id = position.client_order_id
            quantity = float(
                metadata.get("execution_quantity")
                or metadata.get("close_quantity")
                or 0
            )
            attempts = int(
                metadata.get("execution_submission_attempts")
                or metadata.get("close_submission_attempts")
                or 0
            )
            if not client_order_id or quantity <= 0:
                continue

            if attempts >= 3:
                resolved = self._resolve_unknown_close(
                    position_store=position_store,
                    position=position,
                    client_order_id=client_order_id,
                )
                if resolved:
                    continue
                recovered = position_store.recover_client_order_intent(
                    position.id,
                    client_order_id=client_order_id,
                    execution_status=(
                        f"{execution_action}_not_found_after_retries"
                    ),
                )
                if recovered is not None:
                    restoration_error = self._restore_protection_after_failed_close(
                        position.id,
                        required=True,
                    )
                    self._publish_and_record_event(
                        f"position.{execution_action}_submission_failed",
                        {
                            "position_id": position.id,
                            "client_order_id": client_order_id,
                            "attempts": attempts,
                            "protection_restoration_error": restoration_error,
                        },
                    )
                continue

            try:
                result = self.close_executor.close_position(
                    inst_id=position.inst_id,
                    position_side=position.side,
                    quantity=quantity,
                    client_order_id=client_order_id,
                    pos_side=self._exchange_position_side(position),
                ) or {}
            except Exception as exc:
                position_store.merge_metadata(
                    position.id,
                    {
                        "execution_submission_attempts": attempts + 1,
                        "execution_submission_error": str(exc),
                        "close_submission_attempts": attempts + 1,
                        "close_submission_error": str(exc),
                    },
                )
                continue
            order_id = self._extract_order_id(result)
            if order_id:
                position_store.mark_pending_execution(
                    position.id,
                    status="closing" if execution_action == "close" else "reducing",
                    exchange_order_id=order_id,
                    client_order_id=client_order_id,
                    metadata={
                        "execution_status": "submitted",
                        "execution_order_id": order_id,
                        f"{execution_action}_order_id": order_id,
                        "execution_submission_attempts": attempts + 1,
                        "close_submission_attempts": attempts + 1,
                    },
                )
                self._publish_and_record_event(
                    f"position.{execution_action}_submitted",
                    {
                        "position_id": position.id,
                        "client_order_id": client_order_id,
                        "exchange_order_id": order_id,
                        "retry": True,
                    },
                )
            else:
                position_store.merge_metadata(
                    position.id,
                    {
                        "execution_submission_attempts": attempts + 1,
                        "close_submission_attempts": attempts + 1,
                    },
                )
        return touched

    def _resolve_unknown_close(
        self,
        *,
        position_store: LogicalPositionStore,
        position: LogicalPositionRecord,
        client_order_id: str,
    ) -> bool:
        client = getattr(self.close_executor, "client", None)
        if client is None:
            return False
        try:
            orders = client.get_order(
                position.inst_id,
                client_order_id=client_order_id,
            )
        except Exception:
            return True
        if len(orders) != 1:
            return False
        order_id = self._extract_order_id(orders[0])
        if not order_id:
            return True
        position_store.mark_pending_execution(
            position.id,
            status=position.status,
            exchange_order_id=order_id,
            client_order_id=client_order_id,
            metadata={
                "execution_status": "exchange_order_recovered",
                "execution_order_id": order_id,
                f"{self._metadata_order_action(position)}_order_id": order_id,
            },
        )
        return True

    def _restore_protection_after_failed_close(
        self,
        position_id: str,
        *,
        required: bool,
    ) -> str:
        if not required:
            return ""
        if self.protection_service is None:
            error = "protective stop was canceled but cannot be restored"
            self._disable_entries_after_protection_failure()
            return error
        try:
            self.protection_service.protect(position_id)
        except PositionProtectionError as exc:
            self._disable_entries_after_protection_failure()
            return f"protective stop restoration failed: {exc}"
        return ""

    def _disable_entries_after_protection_failure(self) -> None:
        AccountRiskStore(self.store.db_path).set_entries_enabled(False)
        disable_entry_order_placement()

    def _publish_and_record_event(self, event_type: str, payload: dict) -> None:
        self.publish_event(event_type, payload)
        self._record_audit_event(event_type, payload)

    def _record_audit_event(self, event_type: str, payload: dict) -> None:
        try:
            self.audit_store.create(
                type=event_type,
                source=self.name,
                payload=payload,
            )
        except Exception as exc:
            logger.warning("Failed to persist audit event %s: %s", event_type, exc)

    def _try_record_audit_event(self, event_type: str, payload: dict) -> bool:
        try:
            self.audit_store.create(type=event_type, source=self.name, payload=payload)
            return True
        except Exception as exc:
            logger.error("Failed to persist required audit event %s: %s", event_type, exc)
            return False

    @staticmethod
    def _extract_order_id(result: dict[str, Any]) -> str:
        direct = result.get("ordId") or result.get("order_id")
        if direct:
            return str(direct)
        data = result.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return str(data[0].get("ordId") or data[0].get("order_id") or "")
        return ""

    def _exchange_position_side(self, position: LogicalPositionRecord) -> str:
        try:
            metadata = json.loads(position.metadata_json or "{}")
        except json.JSONDecodeError:
            metadata = {}
        value = metadata.get("position_side") if isinstance(metadata, dict) else ""
        if value:
            return str(value)
        if self.runtime is not None:
            snapshot = self.runtime.get_value("account.snapshot") or {}
            for exchange_position in snapshot.get("positions", []):
                if exchange_position.get("inst_id") != position.inst_id:
                    continue
                exchange_side = str(exchange_position.get("pos_side") or "")
                if exchange_side in {"net", position.side}:
                    return exchange_side
        return ""

    @staticmethod
    def _metadata_order_action(position: LogicalPositionRecord) -> str:
        try:
            metadata = json.loads(position.metadata_json or "{}")
        except json.JSONDecodeError:
            return "close"
        action = str(metadata.get("order_action") or "close")
        return action if action in {"close", "reduce"} else "close"

    def _legacy_rule_info(self, trade: TradeRecord) -> list[dict]:
        return [
            {"name": group.name, "id": group.id, "enabled": enabled}
            for group, enabled in self.store.get_trade_rules(trade.id)
        ]

    def _pnl_pct(self, position: LogicalPositionRecord, current_price: float) -> float:
        if position.entry_price <= 0:
            return 0.0
        if position.side == "long":
            return ((current_price - position.entry_price) / position.entry_price) * 100
        return ((position.entry_price - current_price) / position.entry_price) * 100

    def teardown(self) -> None:
        logger.info("PositionManagerService shutting down.")
