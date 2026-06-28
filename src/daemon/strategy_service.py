"""Daemon service for persisted signal-based strategy execution."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.daemon.service import DaemonService
from src.data.candles import CandleManager
from src.exchange.client import OKXClient
from src.trading.action_policy import BTCRegimeActionPolicy
from src.trading.account_risk import AccountRiskStore
from src.trading.audit_event_store import AuditEventStore
from src.trading.executor import Executor
from src.trading.entry_control import ENTRY_EXECUTION_LOCK
from src.trading.logical_position_store import (
    LogicalPositionAllocation,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.signal_context import (
    build_signal_context_from_candles,
    collect_signal_requirements,
)
from src.trading.signal_engine import SignalEvaluationResult, SignalExpressionEngine
from src.trading.strategy_runtime import (
    candle_bar,
    close_condition_specs,
    compose_entry_expression,
    entry_limit_price,
    exchange_protection_prices,
    order_size,
    position_side,
    resolve_self_symbol,
    validate_strategy_for_execution,
)
from src.trading.strategy_store import StrategyRecord, StrategyStore
from src.trading.trade_store import TradeRecord, TradeStore
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


class StrategyService(DaemonService):
    """Evaluate enabled persisted strategies and execute signal edges once."""

    name = "strategy"
    interval = 10.0

    def __init__(
        self,
        dry_run: bool = True,
        *,
        trade_store: TradeStore | None = None,
        audit_store: AuditEventStore | None = None,
        strategy_store: StrategyStore | None = None,
    ) -> None:
        super().__init__()
        self.dry_run = dry_run
        self.trade_store = trade_store or TradeStore()
        self.position_store = LogicalPositionStore(self.trade_store.db_path)
        self.audit_store = audit_store or AuditEventStore(self.trade_store.db_path)
        self.strategy_store = strategy_store or StrategyStore(self.trade_store.db_path)
        self.client: OKXClient | None = None
        self.candle_manager: CandleManager | None = None
        self.executor: Executor | None = None
        self.signal_engine = SignalExpressionEngine()
        self.action_policy = BTCRegimeActionPolicy()
        self.signals_history: list[dict[str, Any]] = []
        self.decisions_history: list[dict[str, Any]] = []

    def setup(self) -> None:
        self.client = OKXClient()
        self.candle_manager = CandleManager(self.client)
        self.executor = Executor(
            self.client,
            dry_run=self.dry_run,
            risk_store=AccountRiskStore(self.trade_store.db_path),
        )
        logger.info("StrategyService setup complete. Dry run: %s", self.dry_run)

    def tick(self) -> None:
        if self.candle_manager is None or self.executor is None:
            raise RuntimeError("StrategyService.setup() must complete before tick()")

        observed_at = datetime.now(timezone.utc).isoformat()
        status: dict[str, Any] = {
            "status": "RUNNING",
            "last_update": observed_at,
            "dry_run": self.dry_run,
            "enabled_strategies": 0,
            "signals": self.signals_history[-10:],
            "decisions": self.decisions_history[-20:],
            "errors": [],
        }

        if not self.dry_run:
            self._retry_emergency_closes(status)

        if not self.dry_run and not self._execution_ingestion_ready():
            message = (
                "Live entries blocked until REST execution catch-up is current "
                "and the private order stream is connected"
            )
            status["errors"].append(message)
            if self.runtime is not None:
                self.runtime.set_value("strategy.status", status)
            self.publish_event("strategy.execution_not_ready", {"reason": message})
            return

        strategies = self.strategy_store.list(enabled=True)
        status["enabled_strategies"] = len(strategies)
        for strategy in strategies:
            errors = validate_strategy_for_execution(strategy, self.strategy_store)
            if errors:
                message = f"Strategy {strategy.id} is not executable: {'; '.join(errors)}"
                logger.error(message)
                status["errors"].append(message)
                continue
            self._evaluate_strategy(strategy, observed_at, status)

        if self.runtime is not None:
            self.runtime.set_value("strategy.decisions", self.decisions_history[-20:])
            self.runtime.set_value("strategy.status", status)

    def _evaluate_strategy(
        self,
        strategy: StrategyRecord,
        observed_at: str,
        status: dict[str, Any],
    ) -> None:
        expression = compose_entry_expression(strategy, self.strategy_store)
        side = position_side(strategy)
        for pair in strategy.target_instruments:
            try:
                resolved = resolve_self_symbol(expression, pair)
                context = self._build_signal_context(strategy, pair, resolved)
                evaluation = self.signal_engine.evaluate(resolved, context=context)
                if not evaluation.valid:
                    raise ValueError("; ".join(evaluation.errors))
                should_trigger = self.strategy_store.record_evaluation(
                    strategy.id,
                    pair,
                    matched=evaluation.matched,
                )
                self.publish_event(
                    "strategy.signal_evaluated",
                    {
                        "strategy_id": strategy.id,
                        "pair": pair,
                        "matched": evaluation.matched,
                        "triggered": should_trigger,
                        "evidence": evaluation.evidence,
                        "time": observed_at,
                    },
                )
                if not should_trigger:
                    continue
                entry_price = float(context["prices"][pair])
                signal_entry = self._process_match(
                    strategy=strategy,
                    pair=pair,
                    side=side,
                    entry_price=entry_price,
                    requested_size=order_size(strategy, pair) or "",
                    evaluation=evaluation,
                    btc_regime=(
                        self.runtime.get_value("market.btc_regime")
                        if self.runtime is not None
                        else None
                    ),
                    observed_at=observed_at,
                )
                status["decisions"] = self.decisions_history[-20:]
                if signal_entry is not None:
                    self.signals_history.append(signal_entry)
                    status["signals"] = self.signals_history[-10:]
                    self.publish_event("strategy.signal", signal_entry)
            except Exception as exc:
                logger.exception("Error evaluating strategy %s for %s", strategy.id, pair)
                status["errors"].append(f"{strategy.id}/{pair}: {exc}")
                self.publish_event(
                    "strategy.error",
                    {
                        "strategy_id": strategy.id,
                        "pair": pair,
                        "time": observed_at,
                        "error": str(exc),
                    },
                )

    def _build_signal_context(
        self,
        strategy: StrategyRecord,
        pair: str,
        expression: dict[str, Any],
    ) -> dict[str, Any]:
        if self.candle_manager is None:
            raise RuntimeError("Candle manager is unavailable")
        requirements = collect_signal_requirements(expression)
        symbols = set(requirements["symbols"])
        symbols.add(pair)
        primary_bar = candle_bar(strategy)
        bars = {primary_bar, *requirements["timeframes"]}
        context: dict[str, Any] = {
            "prices": {},
            "changes_pct": {},
            "volume_ratios": {},
            "source": {},
        }
        for bar in bars:
            candles = {
                symbol: self.candle_manager.fetch(symbol, bar, limit=120)
                for symbol in symbols
            }
            generated = build_signal_context_from_candles(
                candles,
                bar=bar,
                windows_seconds=set(requirements["windows_seconds"]),
            )
            for key in ("prices", "changes_pct", "volume_ratios"):
                context[key].update(generated[key])
            context["source"][bar] = generated["source"]
        if pair not in context["prices"]:
            raise ValueError(f"No current price available for {pair}")
        return context

    def _process_match(
        self,
        *,
        strategy: StrategyRecord,
        pair: str,
        side: str,
        entry_price: float,
        requested_size: str,
        evaluation: SignalEvaluationResult,
        btc_regime: dict[str, Any] | None,
        observed_at: str,
    ) -> dict[str, Any] | None:
        if self.executor is None:
            raise RuntimeError("Executor is required")
        if not self.dry_run and not self._execution_ingestion_ready():
            logger.warning("Live strategy entry blocked because execution ingestion is not ready")
            return None
        with ENTRY_EXECUTION_LOCK:
            return self._process_match_locked(
                strategy=strategy,
                pair=pair,
                side=side,
                entry_price=entry_price,
                requested_size=requested_size,
                evaluation=evaluation,
                btc_regime=btc_regime,
                observed_at=observed_at,
            )

    def _process_match_locked(
        self,
        *,
        strategy: StrategyRecord,
        pair: str,
        side: str,
        entry_price: float,
        requested_size: str,
        evaluation: SignalEvaluationResult,
        btc_regime: dict[str, Any] | None,
        observed_at: str,
    ) -> dict[str, Any] | None:
        if self.executor is None:
            raise RuntimeError("Executor is required")

        decision = self.action_policy.evaluate(
            pair=pair,
            position_side=side,
            btc_regime=btc_regime,
        )
        decision_id = uuid4().hex
        decision_entry: dict[str, Any] = {
            **decision.to_dict(),
            "id": decision_id,
            "correlation_id": decision_id,
            "strategy_id": strategy.id,
            "time": observed_at,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "setup_reason": "persisted entry signal matched",
            "entry_price": entry_price,
            "signal_evidence": evaluation.evidence,
            "dry_run": self.dry_run,
            "execution_status": "pending" if decision.allowed else "blocked",
            "execution_result": {},
        }
        self.decisions_history.append(decision_entry)
        audit_saved = self._save_decision(decision_entry)
        self.publish_event("strategy.action_decision", decision_entry)
        self._publish_decision_snapshot()

        if not decision.allowed:
            return None
        if not audit_saved and not self.dry_run:
            decision_entry.update(
                {
                    "allowed": False,
                    "reason": "blocked: pre-execution audit persistence failed",
                    "execution_status": "audit_failed",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self.publish_event("strategy.execution_blocked", decision_entry)
            self._publish_decision_snapshot()
            return None

        try:
            order_price = entry_limit_price(strategy, entry_price)
            risk_approval = self.executor.approve_entry(
                inst_id=pair,
                requested_size=requested_size,
                entry_price=order_price,
            )
        except Exception as exc:
            decision_entry.update(
                {
                    "allowed": False,
                    "reason": f"blocked: account risk check failed: {exc}",
                    "execution_status": "risk_blocked",
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._save_decision(decision_entry)
            self._save_and_publish_lifecycle_event(
                "strategy.execution_blocked",
                decision_entry,
            )
            self._publish_decision_snapshot()
            return None

        decision_entry["risk_approval"] = risk_approval.to_dict()
        decision_entry["order_price"] = order_price

        stop_loss_price, take_profit_price = exchange_protection_prices(
            strategy,
            self.strategy_store,
            pair,
        )
        try:
            trade, position = self._prepare_open_unit(
                strategy=strategy,
                pair=pair,
                side=side,
                entry_price=entry_price,
                requested_size=requested_size,
                evaluation=evaluation,
                btc_regime=btc_regime,
                decision_id=decision_id,
            )
        except Exception as exc:
            logger.exception("Failed to persist entry intent %s", decision_id)
            decision_entry.update(
                {
                    "execution_status": "persistence_failed",
                    "persistence_error": str(exc),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self._save_decision(decision_entry)
            self._save_and_publish_lifecycle_event(
                "strategy.execution_failed",
                decision_entry,
            )
            self._publish_decision_snapshot()
            return None

        result = self.executor.execute(
            inst_id=pair,
            position_side=side,
            entry_price=order_price,
            requested_size=requested_size,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            client_order_id=decision_id,
            risk_approval=risk_approval,
        ) or {}
        if result and self.dry_run:
            execution_status = "simulated"
        elif result and result.get("maybechProtectionVerified") is True:
            execution_status = "submitted"
        elif result and result.get("maybechEmergencyCloseRequired") is True:
            execution_status = "emergency_close_required"
        elif result and result.get("maybechCancelRequested") is True:
            execution_status = "protection_failed_canceling"
        elif result:
            execution_status = "protection_failed_unresolved"
        else:
            execution_status = "failed"
        lifecycle: dict[str, Any] = {
            "execution_status": execution_status,
            "execution_result": result,
            "order_id": self._extract_order_id(result),
            "trade_id": trade.id,
            "position_id": position.id,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        if result:
            try:
                position = self._record_entry_submission(
                    trade=trade,
                    position=position,
                    client_order_id=decision_id,
                    requested_size=float(requested_size),
                    execution_result=result,
                    execution_status=execution_status,
                )
                if result.get("maybechEmergencyCloseRequired") is True:
                    position, close_result, execution_status = (
                        self._submit_emergency_close(
                            position=position,
                            correlation_id=decision_id,
                            client_order_id=str(
                                result.get("maybechEmergencyCloseClientOrderId") or ""
                            ),
                            quantity=float(
                                result.get("maybechEmergencyCloseQuantity") or 0
                            ),
                            parent_order_id=self._extract_order_id(result),
                        )
                    )
                    result["maybechEmergencyCloseResult"] = close_result
                    result["maybechEmergencyCloseOrderId"] = self._extract_order_id(
                        close_result
                    )
                    lifecycle.update(
                        {
                            "execution_status": execution_status,
                            "execution_result": result,
                            "emergency_close_order_id": result[
                                "maybechEmergencyCloseOrderId"
                            ],
                        }
                    )
            except Exception as exc:
                logger.exception("Failed to persist submitted strategy action %s", decision_id)
                execution_status = "persistence_failed"
                lifecycle.update(
                    {"execution_status": "persistence_failed", "persistence_error": str(exc)}
                )
        else:
            self.position_store.recover_client_order_intent(
                position.id,
                client_order_id=decision_id,
                execution_status="submission_failed",
            )
            self.trade_store.mark_trade_failed(
                trade.id,
                reason="entry order submission failed",
            )

        decision_entry.update(lifecycle)
        self._save_decision(decision_entry)
        event_type = (
            "strategy.execution_result"
            if execution_status in {"simulated", "submitted"}
            else "strategy.execution_failed"
        )
        self._save_and_publish_lifecycle_event(event_type, decision_entry)
        self._publish_decision_snapshot()
        return {
            "strategy_id": strategy.id,
            "pair": pair,
            "signal": side,
            "price": entry_price,
            "time": observed_at,
            "result": execution_status,
            "decision": decision.reason,
            "correlation_id": decision_id,
        }

    def _execution_ingestion_ready(self) -> bool:
        if self.runtime is None:
            return False
        status = self.runtime.get_value("execution.fills.status")
        return bool(
            isinstance(status, dict)
            and status.get("caught_up") is True
            and status.get("websocket_connected") is True
        )

    def _retry_emergency_closes(self, status: dict[str, Any]) -> None:
        for position in self.position_store.list_pending_executions():
            if position.status != "closing" or position.exchange_order_id:
                continue
            try:
                metadata = json.loads(position.metadata_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(metadata, dict) or metadata.get("emergency_close") is not True:
                continue
            client_order_id = position.client_order_id
            quantity = float(metadata.get("close_quantity") or 0)
            if not client_order_id or quantity <= 0:
                status["errors"].append(
                    f"Emergency close intent {position.id} is incomplete"
                )
                continue
            updated, close_result, execution_status = self._place_emergency_close(
                position=position,
                client_order_id=client_order_id,
                quantity=quantity,
            )
            if execution_status == "emergency_close_submitted":
                self.audit_store.create(
                    type="strategy.emergency_close_retried",
                    source=self.name,
                    payload={
                        "strategy_id": updated.strategy_id,
                        "position_id": updated.id,
                        "client_order_id": client_order_id,
                        "exchange_order_id": self._extract_order_id(close_result),
                    },
                )
            else:
                status["errors"].append(
                    f"Emergency close retry remains pending for {position.id}"
                )

    def _prepare_open_unit(
        self,
        *,
        strategy: StrategyRecord,
        pair: str,
        side: str,
        entry_price: float,
        requested_size: str,
        evaluation: SignalEvaluationResult,
        btc_regime: dict[str, Any] | None,
        decision_id: str,
    ) -> tuple[TradeRecord, LogicalPositionRecord]:
        metadata = {
            "correlation_id": decision_id,
            "client_order_id": decision_id,
            "execution_status": "prepared",
            "expected_quantity": float(requested_size),
            "order_action": "open",
            "signal_evidence": evaluation.evidence,
        }
        trade = TradeRecord(
            strategy_id=strategy.id,
            inst_id=pair,
            side=side,
            entry_price=entry_price,
            signal_reason="persisted entry signal matched",
            btc_price_at_entry=btc_regime.get("price") if btc_regime else None,
            status="pending_open",
            metadata_json=json.dumps(metadata, separators=(",", ":"), sort_keys=True),
        )
        self.trade_store.save_trade(trade)
        position = LogicalPositionRecord.from_trade(trade)
        position.client_order_id = decision_id
        self.position_store.save(position)

        for index, spec in enumerate(close_condition_specs(strategy, self.strategy_store)):
            created = self.position_store.create_close_condition(
                position_id=position.id,
                purpose=str(spec.get("purpose") or "exit"),
                expression=resolve_self_symbol(spec["expression"], pair),
                enabled=bool(spec.get("enabled", True)),
                metadata={
                    **(spec.get("metadata") if isinstance(spec.get("metadata"), dict) else {}),
                    "source_strategy_id": strategy.id,
                    "source_index": index,
                },
            )
            if created is None:
                raise RuntimeError("Failed to persist default close condition")

        return trade, position

    def _record_entry_submission(
        self,
        *,
        trade: TradeRecord,
        position: LogicalPositionRecord,
        client_order_id: str,
        requested_size: float,
        execution_result: dict[str, Any],
        execution_status: str,
    ) -> LogicalPositionRecord:
        order_id = self._extract_order_id(execution_result)
        if not order_id:
            raise ValueError("Order submission response is missing ordId")
        linked = self.position_store.link_exchange_order(
            position.id,
            client_order_id=client_order_id,
            exchange_order_id=order_id,
            metadata={
                "execution_status": execution_status,
                "execution_result": execution_result,
                "exchange_order_id": order_id,
            },
        )
        if linked is None:
            raise RuntimeError("Prepared position could not link exchange order")
        position = linked

        if self.dry_run:
            updated = self.position_store.record_allocation(
                LogicalPositionAllocation(
                    id=f"dry-fill-{client_order_id}",
                    position_id=position.id,
                    action="open",
                    quantity=requested_size,
                    price=position.entry_price,
                    exchange_order_id=order_id,
                    reason="confirmed dry-run entry",
                    metadata_json=json.dumps(
                        {
                            "confirmation_source": "dry_run",
                            "correlation_id": client_order_id,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
            if updated is not None:
                position = updated
            self.trade_store.mark_trade_open(trade.id, entry_price=position.entry_price)
            tracked = self.position_store.update_execution_tracking(
                position.id,
                exchange_order_id=order_id,
                execution_status="filled",
                completed=True,
            )
            if tracked is not None:
                position = tracked
        return position

    def _submit_emergency_close(
        self,
        *,
        position: LogicalPositionRecord,
        correlation_id: str,
        client_order_id: str,
        quantity: float,
        parent_order_id: str,
    ) -> tuple[LogicalPositionRecord, dict[str, Any], str]:
        if self.executor is None:
            raise RuntimeError("Executor is required")
        if not client_order_id or not parent_order_id or quantity <= 0:
            raise ValueError("Emergency close intent is incomplete")
        claimed = self.position_store.claim_pending_execution(
            position.id,
            expected_status="pending_open",
            status="closing",
            client_order_id=client_order_id,
            metadata={
                "correlation_id": correlation_id,
                "client_order_id": client_order_id,
                "emergency_close_client_order_id": client_order_id,
                "order_action": "close",
                "close_reason": "active attached protection could not be verified",
                "close_quantity": quantity,
                "previous_exchange_order_id": parent_order_id,
                "emergency_close": True,
                "execution_status": "emergency_close_submitting",
            },
        )
        if claimed is None:
            raise RuntimeError("Emergency close intent could not claim logical position")
        return self._place_emergency_close(
            position=claimed,
            client_order_id=client_order_id,
            quantity=quantity,
        )

    def _place_emergency_close(
        self,
        *,
        position: LogicalPositionRecord,
        client_order_id: str,
        quantity: float,
    ) -> tuple[LogicalPositionRecord, dict[str, Any], str]:
        if self.executor is None:
            raise RuntimeError("Executor is required")
        close_result = self.executor.close_position(
            inst_id=position.inst_id,
            position_side=position.side,
            quantity=quantity,
            client_order_id=client_order_id,
            pos_side="net",
        ) or {}
        exchange_order_id = self._extract_order_id(close_result)
        if not exchange_order_id:
            pending = self.position_store.merge_metadata(
                position.id,
                {
                    "execution_status": "emergency_close_retry_pending",
                    "emergency_close_last_attempt_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                },
            )
            if pending is None:
                raise RuntimeError("Emergency close retry state could not be persisted")
            return pending, close_result, "emergency_close_failed"

        updated = self.position_store.mark_pending_execution(
            position.id,
            status="closing",
            exchange_order_id=exchange_order_id,
            client_order_id=client_order_id,
            metadata={
                "emergency_close_order_id": exchange_order_id,
                "execution_status": "emergency_close_submitted",
            },
        )
        if updated is None:
            raise RuntimeError("Emergency close order could not link to logical position")
        return updated, close_result, "emergency_close_submitted"

    def _save_decision(self, payload: dict[str, Any]) -> bool:
        try:
            self.audit_store.create(
                id=str(payload["id"]),
                type="strategy.action_decision",
                source=self.name,
                payload=payload,
                created_at=str(payload["created_at"]),
            )
            return True
        except Exception as exc:
            logger.error("Failed to persist strategy decision %s: %s", payload["id"], exc)
            self.publish_event(
                "strategy.audit_error",
                {"correlation_id": payload["correlation_id"], "error": str(exc)},
            )
            return False

    def _save_and_publish_lifecycle_event(
        self,
        event_type: str,
        decision: dict[str, Any],
    ) -> None:
        payload = {
            key: decision.get(key)
            for key in (
                "strategy_id",
                "correlation_id",
                "pair",
                "signal",
                "dry_run",
                "execution_status",
                "execution_result",
                "order_id",
                "trade_id",
                "position_id",
                "persistence_error",
                "completed_at",
            )
            if decision.get(key) is not None
        }
        try:
            self.audit_store.create(type=event_type, source=self.name, payload=payload)
        except Exception as exc:
            logger.error("Failed to persist lifecycle event %s: %s", event_type, exc)
        self.publish_event(event_type, payload)

    def _publish_decision_snapshot(self) -> None:
        if self.runtime is not None:
            self.runtime.set_value("strategy.decisions", self.decisions_history[-20:])

    @staticmethod
    def _extract_order_id(result: dict[str, Any]) -> str | None:
        direct = result.get("ordId") or result.get("order_id")
        if direct:
            return str(direct)
        data = result.get("data")
        if isinstance(data, list) and data and isinstance(data[0], dict):
            nested = data[0].get("ordId") or data[0].get("order_id")
            return str(nested) if nested else None
        return None

    def teardown(self) -> None:
        logger.info("StrategyService shutting down.")
