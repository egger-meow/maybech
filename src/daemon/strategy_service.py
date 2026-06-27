"""Daemon service for strategy evaluation and audited entry execution."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from src.config.strategy import StrategyConfig
from src.daemon.service import DaemonService
from src.data.candles import CandleManager
from src.exchange.client import OKXClient
from src.strategies.base import Signal, TradeSetup
from src.strategies.momentum import MomentumStrategy
from src.trading.action_policy import BTCRegimeActionPolicy
from src.trading.audit_event_store import AuditEventStore
from src.trading.executor import Executor
from src.trading.logical_position_store import (
    LogicalPositionAllocation,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.rules import PositionRule, RuleGroup
from src.trading.trade_store import TradeRecord, TradeStore
from src.utils.logger import setup_logger


logger = setup_logger(__name__)
TZ_TAIPEI = timezone(timedelta(hours=8))


class StrategyService(DaemonService):
    """Evaluate the configured strategy and retain an audited action lifecycle."""

    name = "strategy"
    interval = 10.0

    def __init__(
        self,
        dry_run: bool = True,
        *,
        trade_store: TradeStore | None = None,
        audit_store: AuditEventStore | None = None,
    ) -> None:
        super().__init__()
        self.dry_run = dry_run
        self.trade_store = trade_store or TradeStore()
        self.position_store = LogicalPositionStore(self.trade_store.db_path)
        self.audit_store = audit_store or AuditEventStore(self.trade_store.db_path)
        self.client: OKXClient | None = None
        self.candle_manager: CandleManager | None = None
        self.strategy: MomentumStrategy | None = None
        self.executor: Executor | None = None
        self.config: StrategyConfig | None = None
        self.action_policy = BTCRegimeActionPolicy()
        self.signals_history: list[dict[str, Any]] = []
        self.decisions_history: list[dict[str, Any]] = []

    def setup(self) -> None:
        """Initialize exchange, candle, strategy, and execution components."""
        self.client = OKXClient()
        self.candle_manager = CandleManager(self.client)
        self.config = StrategyConfig.load(self.trade_store.db_path)
        self.strategy = MomentumStrategy(config=self.config.momentum)
        self.executor = Executor(
            self.client,
            dry_run=self.dry_run,
            order_sizes=self.config.order_size_contracts,
        )
        logger.info(
            "StrategyService setup complete. Strategy: %s. Dry Run: %s",
            self.strategy.name,
            self.dry_run,
        )

    def tick(self) -> None:
        """Fetch candles, evaluate setups, and execute allowed entries."""
        if self.strategy is None or self.candle_manager is None or self.executor is None:
            raise RuntimeError("StrategyService.setup() must complete before tick()")

        config = StrategyConfig.load(self.trade_store.db_path)
        self.config = config
        self.strategy.config = config.momentum
        self.strategy.k_long = config.momentum.k_long
        self.strategy.k_short = config.momentum.k_short
        self.strategy.gap_threshold = config.momentum.gap_threshold
        self.executor.configure_order_sizes(config.order_size_contracts)

        current_time = datetime.now(TZ_TAIPEI).isoformat()
        status: dict[str, Any] = {
            "status": "RUNNING",
            "last_update": current_time,
            "strategy": self.strategy.name,
            "dry_run": self.dry_run,
            "signals": self.signals_history[-10:],
            "decisions": self.decisions_history[-20:],
            "errors": [],
        }

        for pair in config.target_instruments:
            try:
                frame = self.candle_manager.fetch(
                    pair,
                    config.timeframe,
                    limit=100,
                )
                if frame.empty:
                    logger.warning("No data for %s", pair)
                    status["errors"].append(f"No data for {pair}")
                    continue

                signal = self.strategy.generate_signal(frame)
                if signal == Signal.HOLD:
                    continue

                setup = self.strategy.create_setup(frame)
                if setup is None:
                    self._publish_signal_rejected(pair, signal, current_time)
                    continue

                btc_regime = (
                    self.runtime.get_value("market.btc_regime")
                    if self.runtime is not None
                    else None
                )
                signal_entry = self._process_setup(
                    pair=pair,
                    setup=setup,
                    btc_regime=btc_regime,
                    observed_at=current_time,
                )
                status["decisions"] = self.decisions_history[-20:]
                if signal_entry is not None:
                    self.signals_history.append(signal_entry)
                    status["signals"] = self.signals_history[-10:]
                    self.publish_event("strategy.signal", signal_entry)
            except Exception as exc:
                logger.exception("Error processing %s in StrategyService", pair)
                status["errors"].append(f"Error in {pair}: {exc}")
                self.publish_event(
                    "strategy.error",
                    {"pair": pair, "time": current_time, "error": str(exc)},
                )

        if self.runtime is not None:
            self.runtime.set_value("strategy.decisions", self.decisions_history[-20:])

    def _process_setup(
        self,
        *,
        pair: str,
        setup: TradeSetup,
        btc_regime: dict[str, Any] | None,
        observed_at: str,
    ) -> dict[str, Any] | None:
        if self.strategy is None or self.executor is None:
            raise RuntimeError("Strategy and executor are required")

        decision = self.action_policy.evaluate(
            pair=pair,
            setup=setup,
            btc_regime=btc_regime,
        )
        decision_id = uuid4().hex
        decision_entry: dict[str, Any] = {
            **decision.to_dict(),
            "id": decision_id,
            "correlation_id": decision_id,
            "strategy_id": self.strategy.name,
            "time": observed_at,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "setup_reason": setup.reason,
            "entry_price": setup.entry_price,
            "stop_loss": setup.stop_loss,
            "take_profit": setup.take_profit,
            "dry_run": self.dry_run,
            "execution_status": "pending" if decision.allowed else "blocked",
            "execution_result": {},
        }
        self.decisions_history.append(decision_entry)
        audit_saved = self._save_decision(decision_entry)
        self.publish_event("strategy.action_decision", decision_entry)
        self._publish_decision_snapshot()

        if not decision.allowed:
            logger.info("Action blocked for %s: %s", pair, decision.reason)
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

        result = self.executor.execute(pair, setup) or {}
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
                    pair=pair,
                    setup=setup,
                    btc_regime=btc_regime,
                    decision_id=decision_id,
                    execution_result=result,
                )
                lifecycle.update(
                    {
                        "trade_id": trade.id,
                        "position_id": position.id,
                    }
                )
            except Exception as exc:
                logger.exception("Failed to persist submitted strategy action %s", decision_id)
                lifecycle.update(
                    {
                        "execution_status": "persistence_failed",
                        "persistence_error": str(exc),
                    }
                )

        decision_entry.update(lifecycle)
        self._save_decision(decision_entry)
        result_event_type = (
            "strategy.execution_result"
            if execution_status != "failed"
            else "strategy.execution_failed"
        )
        self._save_and_publish_lifecycle_event(result_event_type, decision_entry)
        self._publish_decision_snapshot()

        return {
            "pair": pair,
            "signal": setup.signal.value,
            "price": setup.entry_price,
            "time": observed_at,
            "result": execution_status,
            "decision": decision.reason,
            "correlation_id": decision_id,
        }

    def _record_open_unit(
        self,
        *,
        pair: str,
        setup: TradeSetup,
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
        }
        trade = TradeRecord(
            strategy_id=self.strategy.name if self.strategy is not None else "",
            inst_id=pair,
            side=setup.signal.value,
            entry_price=setup.entry_price,
            signal_reason=setup.reason,
            btc_price_at_entry=btc_regime.get("price") if btc_regime else None,
            status="open" if self.dry_run else "pending_open",
            metadata_json=json.dumps(metadata, separators=(",", ":"), sort_keys=True),
        )
        self.trade_store.save_trade(trade)
        position = LogicalPositionRecord.from_trade(trade)
        self.position_store.save(position)

        if self.dry_run:
            updated = self.position_store.record_allocation(
                LogicalPositionAllocation(
                    id=f"dry-fill-{decision_id}",
                    position_id=position.id,
                    action="open",
                    quantity=self._requested_size(execution_result),
                    price=setup.entry_price,
                    exchange_order_id=self._extract_order_id(execution_result) or "",
                    reason="confirmed dry-run entry",
                    metadata_json=json.dumps(
                        {
                            "confirmation_source": "dry_run",
                            "correlation_id": decision_id,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )
            if updated is not None:
                position = updated

        stop_loss = PositionRule(
            target="self",
            metric="price",
            operator="less_than" if setup.signal == Signal.LONG else "greater_than",
            value=setup.stop_loss,
        )
        self.trade_store.attach_rule_group(
            trade.id,
            RuleGroup(name="Default Stop Loss", rules=[stop_loss]),
        )
        take_profit = PositionRule(
            target="self",
            metric="price",
            operator="greater_than" if setup.signal == Signal.LONG else "less_than",
            value=setup.take_profit,
        )
        self.trade_store.attach_rule_group(
            trade.id,
            RuleGroup(name="Default Take Profit", rules=[take_profit]),
        )
        return trade, position

    @staticmethod
    def _requested_size(execution_result: dict[str, Any]) -> float:
        value = execution_result.get("maybechRequestedSize")
        if value is None:
            raise ValueError("Execution result is missing validated requested size")
        quantity = float(value)
        if quantity <= 0:
            raise ValueError("Execution result requested size must be positive")
        return quantity

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

    def _publish_signal_rejected(
        self,
        pair: str,
        signal: Signal,
        observed_at: str,
    ) -> None:
        logger.warning("Signal %s but setup creation failed for %s", signal, pair)
        self.publish_event(
            "strategy.signal_rejected",
            {
                "pair": pair,
                "signal": signal.value,
                "time": observed_at,
                "reason": "setup creation failed",
            },
        )

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
