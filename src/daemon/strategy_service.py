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
from src.trading.audit_event_store import AuditEventStore
from src.trading.executor import Executor
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
        self.executor = Executor(self.client, dry_run=self.dry_run)
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

        stop_loss_price, take_profit_price = exchange_protection_prices(
            strategy,
            self.strategy_store,
            pair,
        )
        result = self.executor.execute(
            inst_id=pair,
            position_side=side,
            entry_price=entry_price,
            requested_size=requested_size,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        ) or {}
        execution_status = (
            "simulated" if result and self.dry_run else "submitted" if result else "failed"
        )
        lifecycle: dict[str, Any] = {
            "execution_status": execution_status,
            "execution_result": result,
            "order_id": self._extract_order_id(result),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        if result:
            try:
                trade, position = self._record_open_unit(
                    strategy=strategy,
                    pair=pair,
                    side=side,
                    entry_price=entry_price,
                    evaluation=evaluation,
                    btc_regime=btc_regime,
                    decision_id=decision_id,
                    execution_result=result,
                )
                lifecycle.update({"trade_id": trade.id, "position_id": position.id})
            except Exception as exc:
                logger.exception("Failed to persist submitted strategy action %s", decision_id)
                lifecycle.update(
                    {"execution_status": "persistence_failed", "persistence_error": str(exc)}
                )

        decision_entry.update(lifecycle)
        self._save_decision(decision_entry)
        event_type = "strategy.execution_result" if result else "strategy.execution_failed"
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

    def _record_open_unit(
        self,
        *,
        strategy: StrategyRecord,
        pair: str,
        side: str,
        entry_price: float,
        evaluation: SignalEvaluationResult,
        btc_regime: dict[str, Any] | None,
        decision_id: str,
        execution_result: dict[str, Any],
    ) -> tuple[TradeRecord, LogicalPositionRecord]:
        metadata = {
            "correlation_id": decision_id,
            "execution_status": "simulated" if self.dry_run else "submitted",
            "execution_result": execution_result,
            "exchange_order_id": self._extract_order_id(execution_result),
            "expected_quantity": self._requested_size(execution_result),
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
            status="open" if self.dry_run else "pending_open",
            metadata_json=json.dumps(metadata, separators=(",", ":"), sort_keys=True),
        )
        self.trade_store.save_trade(trade)
        position = LogicalPositionRecord.from_trade(trade)
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

        if self.dry_run:
            updated = self.position_store.record_allocation(
                LogicalPositionAllocation(
                    id=f"dry-fill-{decision_id}",
                    position_id=position.id,
                    action="open",
                    quantity=self._requested_size(execution_result),
                    price=entry_price,
                    exchange_order_id=self._extract_order_id(execution_result) or "",
                    reason="confirmed dry-run entry",
                    metadata_json=json.dumps(
                        {"confirmation_source": "dry_run", "correlation_id": decision_id},
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
            if updated is not None:
                position = updated
        return trade, position

    @staticmethod
    def _requested_size(execution_result: dict[str, Any]) -> float:
        value = execution_result.get("maybechRequestedSize")
        if value is None or float(value) <= 0:
            raise ValueError("Execution result is missing a positive validated size")
        return float(value)

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
