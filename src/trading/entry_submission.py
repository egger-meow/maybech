"""Shared entry-order submission core.

Both the persisted-strategy execution loop and operator-initiated manual
opens must submit entries through the exact same risk-approval /
execution-ingestion-readiness / emergency-close-on-protection-failure path,
so real order placement never has two independently-maintained
implementations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.trading.account_risk import EntryRiskApproval
from src.trading.execution_health import evaluate_execution_health
from src.trading.executor import Executor
from src.trading.logical_position_store import (
    LogicalPositionAllocation,
    LogicalPositionProtection,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.trade_store import TradeRecord, TradeStore

logger = logging.getLogger(__name__)


def extract_order_id(result: dict[str, Any]) -> str | None:
    direct = result.get("ordId") or result.get("order_id")
    if direct:
        return str(direct)
    data = result.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        nested = data[0].get("ordId") or data[0].get("order_id")
        return str(nested) if nested else None
    return None


def execution_ingestion_ready(runtime: Any) -> bool:
    """True once REST fill catch-up and the private order stream are healthy."""
    if runtime is None:
        return False
    status = runtime.get_value("execution.fills.status")
    return bool(isinstance(status, dict) and evaluate_execution_health(status)["healthy"])


class EntrySubmitter:
    """Submit a prepared entry order and reconcile the resulting position."""

    def __init__(
        self,
        *,
        executor: Executor,
        position_store: LogicalPositionStore,
        trade_store: TradeStore,
        dry_run: bool,
    ) -> None:
        self.executor = executor
        self.position_store = position_store
        self.trade_store = trade_store
        self.dry_run = dry_run

    def submit(
        self,
        *,
        trade: TradeRecord,
        position: LogicalPositionRecord,
        client_order_id: str,
        requested_size: float,
        entry_price: float,
        stop_loss_price: float,
        take_profit_price: float | None,
        risk_approval: EntryRiskApproval,
    ) -> tuple[LogicalPositionRecord, dict[str, Any], str]:
        result = self.executor.execute(
            inst_id=position.inst_id,
            position_side=position.side,
            entry_price=entry_price,
            requested_size=str(requested_size),
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            client_order_id=client_order_id,
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

        if result:
            try:
                position = self.record_entry_submission(
                    trade=trade,
                    position=position,
                    client_order_id=client_order_id,
                    requested_size=requested_size,
                    execution_result=result,
                    execution_status=execution_status,
                )
                if result.get("maybechEmergencyCloseRequired") is True:
                    position, close_result, execution_status = self.submit_emergency_close(
                        position=position,
                        correlation_id=client_order_id,
                        client_order_id=str(
                            result.get("maybechEmergencyCloseClientOrderId") or ""
                        ),
                        quantity=float(result.get("maybechEmergencyCloseQuantity") or 0),
                        parent_order_id=extract_order_id(result) or "",
                    )
                    result["maybechEmergencyCloseResult"] = close_result
                    result["maybechEmergencyCloseOrderId"] = extract_order_id(close_result)
            except Exception as exc:
                logger.exception(
                    "Failed to persist submitted entry order %s", client_order_id
                )
                execution_status = "persistence_failed"
                result["maybechPersistenceError"] = str(exc)
        else:
            self.position_store.recover_client_order_intent(
                position.id,
                client_order_id=client_order_id,
                execution_status="submission_failed",
            )
            self.trade_store.mark_trade_failed(
                trade.id,
                reason="entry order submission failed",
            )

        return position, result, execution_status

    def record_entry_submission(
        self,
        *,
        trade: TradeRecord,
        position: LogicalPositionRecord,
        client_order_id: str,
        requested_size: float,
        execution_result: dict[str, Any],
        execution_status: str,
    ) -> LogicalPositionRecord:
        order_id = extract_order_id(execution_result)
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
        protection = execution_result.get("maybechProtection")
        active_protection = (
            protection.get("active") if isinstance(protection, dict) else None
        )
        if isinstance(active_protection, dict):
            self.position_store.save_protection(
                LogicalPositionProtection(
                    position_id=position.id,
                    kind="attached_stop",
                    status="active",
                    algo_id=str(active_protection.get("algo_id") or ""),
                    algo_client_order_id=str(
                        active_protection.get("algo_client_order_id") or ""
                    ),
                    quantity=float(active_protection.get("quantity") or 0),
                    stop_loss=float(active_protection.get("stop_loss") or 0),
                    metadata_json=json.dumps(
                        {
                            "take_profit": active_protection.get("take_profit"),
                            "parent_order_id": order_id,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )

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

    def submit_emergency_close(
        self,
        *,
        position: LogicalPositionRecord,
        correlation_id: str,
        client_order_id: str,
        quantity: float,
        parent_order_id: str,
    ) -> tuple[LogicalPositionRecord, dict[str, Any], str]:
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
        return self.place_emergency_close(
            position=claimed,
            client_order_id=client_order_id,
            quantity=quantity,
        )

    def place_emergency_close(
        self,
        *,
        position: LogicalPositionRecord,
        client_order_id: str,
        quantity: float,
    ) -> tuple[LogicalPositionRecord, dict[str, Any], str]:
        close_result = self.executor.close_position(
            inst_id=position.inst_id,
            position_side=position.side,
            quantity=quantity,
            client_order_id=client_order_id,
            pos_side="net",
        ) or {}
        exchange_order_id = extract_order_id(close_result)
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
