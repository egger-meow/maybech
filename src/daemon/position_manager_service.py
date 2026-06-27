"""Position manager daemon evaluates logical-position close conditions.

For each open logical position unit, this service reads current runtime prices,
builds signal evaluation context, evaluates first-class close conditions, and
falls back to legacy trade rule groups for compatibility. Armed live triggers
submit reduce-only closes and wait for confirmed fill allocation.
"""

from __future__ import annotations

import json
import logging
import time
from collections import deque
from typing import Any
from uuid import uuid4

from src.config.strategy import StrategyConfig
from src.data.candles import CandleManager
from src.daemon.service import DaemonService
from src.exchange.client import OKXClient
from src.trading.audit_event_store import AuditEventStore
from src.trading.executor import Executor
from src.trading.logical_position_store import (
    LogicalPositionAllocation,
    LogicalPositionCloseCondition,
    LogicalPositionRecord,
    LogicalPositionStore,
)
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
    ) -> None:
        super().__init__()
        self.store = store
        self.dry_run = dry_run
        self.candle_manager = candle_manager
        self.enable_candle_context = enable_candle_context
        self.candle_bar = candle_bar or StrategyConfig.load(store.db_path).timeframe
        self.candle_limit = candle_limit
        self.audit_store = audit_store or AuditEventStore(store.db_path)
        self.close_executor = close_executor
        self._price_history: dict[str, deque[tuple[float, float]]] = {}
        self._max_history_seconds = 600

    def setup(self) -> None:
        if self.close_executor is None:
            self.close_executor = Executor(OKXClient(), dry_run=self.dry_run)
        logger.info("PositionManagerService setup complete. Dry run: %s", self.dry_run)

    def tick(self) -> None:
        if self.runtime is None:
            return

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
                        },
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
    ) -> dict:
        event_payload = {
            "position_id": position.id,
            "trade_id": position.trade_id,
            "inst_id": position.inst_id,
            "side": position.side,
            "current_price": current_price,
            "exit_reason": exit_reason,
            "strategy_id": position.strategy_id,
            "dry_run": self.dry_run,
            **trigger_payload,
        }

        if not self.dry_run and self.close_executor is None:
            logger.error(
                "LIVE CLOSE BLOCKED: %s %s %s reason=%s. "
                "No exchange close-order executor is wired.",
                position.id,
                position.side,
                position.inst_id,
                exit_reason,
            )
            self._publish_and_record_event("position.close_blocked", {
                **event_payload,
                "reason": "live close order executor is not implemented",
            })
            return {
                "position_id": position.id,
                "trade_id": position.trade_id,
                "inst_id": position.inst_id,
                "side": position.side,
                "action": "manual_close_required",
                "reason": exit_reason,
                "current_price": current_price,
                "strategy_id": position.strategy_id,
                **trigger_payload,
            }

        if not self.dry_run:
            close_quantity = position.remaining_quantity or position.opened_quantity or 0.0
            correlation_id = uuid4().hex
            close_intent = {
                **event_payload,
                "correlation_id": correlation_id,
                "quantity": close_quantity,
                "execution_status": "pending_submission",
            }
            if close_quantity <= 0:
                self._publish_and_record_event(
                    "position.close_submission_failed",
                    {**close_intent, "reason": "position has no allocated quantity"},
                )
                return {**close_intent, "action": "close_submission_failed"}
            if not self._try_record_audit_event("position.close_requested", close_intent):
                self.publish_event(
                    "position.close_blocked",
                    {**close_intent, "reason": "pre-submission audit persistence failed"},
                )
                return {
                    **close_intent,
                    "action": "manual_close_required",
                    "reason": "pre-submission audit persistence failed",
                }

            claimed = position_store.claim_pending_execution(
                position.id,
                expected_status="open",
                status="closing",
                metadata={
                    "correlation_id": correlation_id,
                    "order_action": "close",
                    "close_reason": exit_reason,
                    "close_quantity": close_quantity,
                    "previous_exchange_order_id": position.exchange_order_id,
                    "execution_status": "submitting",
                },
            )
            if claimed is None:
                return {
                    **close_intent,
                    "action": "close_already_pending",
                    "reason": "position was claimed by another close request",
                }

            try:
                result = self.close_executor.close_position(
                    inst_id=position.inst_id,
                    position_side=position.side,
                    quantity=close_quantity,
                    pos_side=self._exchange_position_side(position),
                )
            except Exception as exc:
                position_store.release_pending_execution(
                    position.id,
                    correlation_id=correlation_id,
                    restore_status="open",
                )
                self._publish_and_record_event(
                    "position.close_submission_failed",
                    {**close_intent, "error": str(exc)},
                )
                return {**close_intent, "action": "close_submission_failed"}
            order_id = self._extract_order_id(result)
            if not order_id:
                position_store.release_pending_execution(
                    position.id,
                    correlation_id=correlation_id,
                    restore_status="open",
                )
                self._publish_and_record_event(
                    "position.close_submission_failed",
                    {**close_intent, "execution_result": result},
                )
                return {**close_intent, "action": "close_submission_failed"}

            updated = position_store.mark_pending_execution(
                position.id,
                status="closing",
                exchange_order_id=order_id,
                metadata={
                    "correlation_id": correlation_id,
                    "order_action": "close",
                    "close_order_id": order_id,
                    "close_reason": exit_reason,
                    "close_quantity": close_quantity,
                    "execution_status": "submitted",
                },
            )
            if updated is None:
                self._publish_and_record_event(
                    "position.close_submission_failed",
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
            self._publish_and_record_event("position.close_submitted", submitted_payload)
            return {
                **submitted_payload,
                "action": "close_submitted",
                "status": updated.status,
            }

        close_quantity = position.remaining_quantity or position.opened_quantity or 0.0
        closed_trade = None
        if trade is not None:
            closed_trade = self.store.close_trade(
                trade.id,
                exit_price=current_price,
                exit_reason=exit_reason,
                btc_price_at_exit=btc_price,
            )

        updated_position = position_store.record_allocation(
            LogicalPositionAllocation(
                position_id=position.id,
                action="close",
                quantity=close_quantity,
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
                status="closed",
                remaining_quantity=0.0,
            )

        logger.info(
            "POSITION CLOSED: %s %s %s @ %.4f (reason=%s)",
            position.id,
            position.side,
            position.inst_id,
            current_price,
            exit_reason,
        )
        self._publish_and_record_event("position.closed", {
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
            "action": "closed",
            "reason": exit_reason,
            "current_price": current_price,
            "pnl": None if closed_trade is None else closed_trade.pnl,
            "pnl_pct": None if closed_trade is None else closed_trade.pnl_pct,
            "strategy_id": position.strategy_id,
            **trigger_payload,
        }

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
