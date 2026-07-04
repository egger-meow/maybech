"""Confirmed execution-fill normalization and logical-unit allocation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from src.trading.audit_event_store import AuditEventStore
from src.trading.logical_position_store import (
    AllocationAction,
    LogicalPositionAllocation,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.trade_store import TradeStore
from src.trading.position_rule_model import materialize_position_rule


FillSource = Literal["okx_fill", "dry_run", "recovery"]


@dataclass(frozen=True)
class ConfirmedExecutionFill:
    fill_id: str
    quantity: float
    price: float
    confirmation_source: FillSource
    action: AllocationAction | None = None
    position_id: str = ""
    exchange_order_id: str = ""
    client_order_id: str = ""
    correlation_id: str = ""
    fee: float | None = None
    occurred_at: str = ""
    reason: str = "confirmed execution fill"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AllocationIngestionResult:
    allocation: LogicalPositionAllocation
    position: LogicalPositionRecord
    idempotent: bool
    execution_status: str


class ExecutionAllocationService:
    """Apply confirmed fills once and correlate them with prior decisions."""

    def __init__(
        self,
        trade_store: TradeStore | None = None,
        position_store: LogicalPositionStore | None = None,
        audit_store: AuditEventStore | None = None,
    ) -> None:
        self.trade_store = trade_store or TradeStore()
        self.position_store = position_store or LogicalPositionStore(
            self.trade_store.db_path
        )
        self.audit_store = audit_store or AuditEventStore(self.trade_store.db_path)

    def ingest(self, fill: ConfirmedExecutionFill) -> AllocationIngestionResult:
        existing = self.position_store.get_allocation(fill.fill_id)
        position = self._resolve_position(fill, existing)
        action = (
            fill.action
            if fill.action is not None
            else existing.action
            if existing is not None
            else self._resolve_action(position, fill)
        )
        existing_correlation_id = (
            str(self._allocation_metadata(existing).get("correlation_id") or "")
            if existing is not None
            else ""
        )
        correlation_id = (
            fill.correlation_id
            or existing_correlation_id
            or self._metadata_string(position, "correlation_id")
        )
        allocation = LogicalPositionAllocation(
            id=fill.fill_id,
            position_id=position.id,
            action=action,
            quantity=fill.quantity,
            price=fill.price,
            fee=fill.fee,
            exchange_order_id=fill.exchange_order_id,
            reason=fill.reason,
            created_at=fill.occurred_at,
            metadata_json=json.dumps(
                {
                    **fill.metadata,
                    "confirmation_source": fill.confirmation_source,
                    "correlation_id": correlation_id,
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        if existing is not None and not existing.applied:
            self.position_store.record_allocation(allocation)
            return AllocationIngestionResult(
                allocation=existing,
                position=position,
                idempotent=True,
                execution_status="close_fill_deferred",
            )
        defer_close = (
            existing is None
            and action in {"reduce", "close"}
            and (position.remaining_quantity or position.opened_quantity or 0.0)
            < fill.quantity - 1e-12
            and self.position_store.has_execution_order(position.id, action="open")
        )
        updated = self.position_store.record_allocation(
            allocation,
            apply_to_position=not defer_close,
            allow_open_while_closing=(action == "open"),
        )
        if updated is None:
            raise LookupError(f"Logical position {position.id!r} no longer exists")

        if defer_close:
            tracked = self.position_store.update_execution_tracking(
                updated.id,
                exchange_order_id=allocation.exchange_order_id,
                execution_status="close_fill_deferred",
                completed=True,
            )
            if tracked is not None:
                updated = tracked
            self.audit_store.create(
                id=f"deferred-allocation:{allocation.id}",
                type="position.allocation_deferred",
                source="execution_allocation",
                payload={
                    "position_id": updated.id,
                    "trade_id": updated.trade_id,
                    "fill_id": allocation.id,
                    "exchange_order_id": allocation.exchange_order_id,
                    "action": allocation.action,
                    "quantity": allocation.quantity,
                    "reason": "close fill arrived before opening fill allocation",
                },
                created_at=allocation.created_at,
            )
            return AllocationIngestionResult(
                allocation=allocation,
                position=updated,
                idempotent=False,
                execution_status="close_fill_deferred",
            )

        execution_status = self._update_trade_and_status(updated, allocation)
        if allocation.action == "open":
            self._materialize_confirmed_entry_rules(updated, allocation)
        if allocation.action == "reduce":
            self._complete_one_shot_rule(updated, allocation)
        tracked = self.position_store.update_execution_tracking(
            updated.id,
            exchange_order_id=allocation.exchange_order_id,
            execution_status=execution_status,
            completed=(
                execution_status in {"filled", "closed"}
                or (
                    allocation.action == "reduce"
                    and execution_status == "reduced"
                )
            ),
        )
        if tracked is not None:
            updated = tracked
        if existing is None:
            self._record_allocation_audit(
                updated,
                allocation,
                correlation_id=correlation_id,
                confirmation_source=fill.confirmation_source,
                execution_status=execution_status,
            )
        decision_allocation = allocation
        if allocation.action == "open":
            updated, deferred = self.position_store.apply_deferred_allocations(
                updated.id
            )
            if updated is None:
                raise LookupError(f"Logical position {position.id!r} no longer exists")
            for deferred_allocation in deferred:
                decision_allocation = deferred_allocation
                execution_status = self._update_trade_and_status(
                    updated,
                    deferred_allocation,
                )
                tracked = self.position_store.merge_metadata(
                    updated.id,
                    {
                        "execution_status": execution_status,
                        "completed_order_id": deferred_allocation.exchange_order_id,
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                if tracked is not None:
                    updated = tracked
                deferred_metadata = self._allocation_metadata(deferred_allocation)
                confirmation_source = deferred_metadata.get("confirmation_source")
                if confirmation_source not in {"okx_fill", "dry_run", "recovery"}:
                    confirmation_source = fill.confirmation_source
                self._record_allocation_audit(
                    updated,
                    deferred_allocation,
                    correlation_id=correlation_id,
                    confirmation_source=confirmation_source,
                    execution_status=execution_status,
                )
        if correlation_id:
            self._update_correlated_decision(
                updated,
                decision_allocation,
                correlation_id=correlation_id,
                execution_status=execution_status,
            )
        protection = self.position_store.get_protection(position.id)
        if protection is not None and decision_allocation.action in {"reduce", "close"}:
            triggered_by_this_order = (
                protection.trigger_order_id == decision_allocation.exchange_order_id
            )
            if updated.status == "closed" and protection.status in {
                "canceled",
                "triggered",
            }:
                self.position_store.update_protection(
                    position.id,
                    status="exhausted",
                    metadata={
                        "exhausted_by_fill_id": decision_allocation.id,
                        "remaining_quantity": updated.remaining_quantity,
                    },
                )
            elif triggered_by_this_order:
                self.position_store.update_protection(
                    position.id,
                    status="triggered",
                    metadata={
                        "last_trigger_fill_id": decision_allocation.id,
                        "remaining_quantity": updated.remaining_quantity,
                    },
                )
        return AllocationIngestionResult(
            allocation=existing or allocation,
            position=updated,
            idempotent=existing is not None,
            execution_status=execution_status,
        )

    def _complete_one_shot_rule(
        self,
        position: LogicalPositionRecord,
        allocation: LogicalPositionAllocation,
    ) -> None:
        try:
            metadata = json.loads(position.metadata_json or "{}")
        except json.JSONDecodeError:
            return
        condition_id = str(metadata.get("rule_condition_id") or "")
        if not condition_id:
            return
        condition = self.position_store.get_close_condition(position.id, condition_id)
        if condition is None:
            return
        action = condition.metadata.get("rule_definition", {}).get("action", {})
        if action.get("type") != "reduce_position":
            return
        self.position_store.update_close_condition(
            position.id,
            condition.id,
            enabled=False,
            metadata={
                **condition.metadata,
                "execution_state": {
                    "status": "completed",
                    "fill_id": allocation.id,
                    "quantity": allocation.quantity,
                    "price": allocation.price,
                },
            },
        )

    def _materialize_confirmed_entry_rules(
        self,
        position: LogicalPositionRecord,
        allocation: LogicalPositionAllocation,
    ) -> None:
        protection = self.position_store.get_protection(position.id)
        for condition in self.position_store.list_close_conditions(position.id):
            if not isinstance(condition.metadata.get("template_definition"), dict):
                continue
            materialized = materialize_position_rule(
                {
                    "purpose": condition.purpose,
                    "expression": condition.expression,
                    "enabled": condition.enabled,
                    "metadata": condition.metadata,
                },
                entry_price=position.entry_price,
                inst_id=position.inst_id,
                side=position.side,
                basis="confirmed_average_entry",
            )
            if (
                condition.purpose == "stop_loss"
                and protection is not None
                and self._stop_is_looser(
                    side=position.side,
                    candidate=materialized["expression"].get("value"),
                    confirmed=protection.stop_loss,
                )
            ):
                self.audit_store.create(
                    type="position.rule_materialization_skipped",
                    source="execution_allocation",
                    payload={
                        "position_id": position.id,
                        "condition_id": condition.id,
                        "fill_id": allocation.id,
                        "reason": "candidate would loosen confirmed protection",
                        "confirmed_stop": protection.stop_loss,
                        "candidate_stop": materialized["expression"].get("value"),
                    },
                )
                continue
            if (
                condition.purpose == "stop_loss"
                and protection is not None
                and materialized["expression"] != condition.expression
            ):
                self.position_store.update_close_condition(
                    position.id,
                    condition.id,
                    metadata={
                        **condition.metadata,
                        "pending_materialization": {
                            "status": "pending_exchange_amend",
                            "expression": materialized["expression"],
                            "metadata": materialized["metadata"],
                            "entry_fill_id": allocation.id,
                        },
                    },
                )
                self.position_store.merge_metadata(position.id, {
                    "requires_manual_review": True,
                    "protection_materialization_pending": True,
                })
                self.audit_store.create(
                    type="position.rule_materialization_pending",
                    source="execution_allocation",
                    payload={
                        "position_id": position.id,
                        "condition_id": condition.id,
                        "fill_id": allocation.id,
                        "confirmed_entry_price": position.entry_price,
                        "target_expression": materialized["expression"],
                    },
                )
                continue
            self.position_store.update_close_condition(
                position.id,
                condition.id,
                expression=materialized["expression"],
                metadata=materialized["metadata"],
            )

    @staticmethod
    def _stop_is_looser(*, side: str, candidate: object, confirmed: object) -> bool:
        candidate_price = float(candidate)
        confirmed_price = float(confirmed)
        return (
            candidate_price < confirmed_price
            if side == "long"
            else candidate_price > confirmed_price
        )

    def _resolve_position(
        self,
        fill: ConfirmedExecutionFill,
        existing: LogicalPositionAllocation | None,
    ) -> LogicalPositionRecord:
        if existing is not None:
            position = self.position_store.get(existing.position_id)
        elif fill.position_id:
            position = self.position_store.get(fill.position_id)
        else:
            position = self.position_store.get_by_exchange_order_id(
                fill.exchange_order_id
            )
            if position is None and fill.client_order_id:
                position = self.position_store.get_by_client_order_id(
                    fill.client_order_id
                )
                if position is not None and fill.exchange_order_id:
                    linked = self.position_store.link_exchange_order(
                        position.id,
                        client_order_id=fill.client_order_id,
                        exchange_order_id=fill.exchange_order_id,
                        metadata={"execution_status": "exchange_order_recovered_from_fill"},
                    )
                    if linked is not None:
                        position = linked
        if position is None:
            reference = fill.position_id or fill.exchange_order_id or fill.client_order_id
            raise LookupError(f"No logical position matches confirmed fill {reference!r}")
        return position

    def _resolve_action(
        self,
        position: LogicalPositionRecord,
        fill: ConfirmedExecutionFill,
    ) -> AllocationAction:
        if fill.action is not None:
            return fill.action
        order = self.position_store.get_execution_order(fill.exchange_order_id)
        if order is not None and order.get("action") in {"open", "reduce", "close"}:
            return order["action"]
        metadata = ExecutionAllocationService._metadata(position)
        order_action = metadata.get("order_action")
        if order_action in {"open", "reduce", "close"}:
            return order_action
        if position.status in {"planned", "pending_open"}:
            return "open"
        if position.status == "reducing":
            return "reduce"
        if position.status == "closing":
            return "close"
        raise ValueError(
            f"Cannot infer fill action for {position.status} position {position.id!r}"
        )

    def _update_trade_and_status(
        self,
        position: LogicalPositionRecord,
        allocation: LogicalPositionAllocation,
    ) -> str:
        if allocation.action == "open":
            if position.trade_id:
                self.trade_store.mark_trade_open(
                    position.trade_id,
                    entry_price=position.entry_price,
                )
            expected_quantity = self._metadata_float(position, "expected_quantity")
            if expected_quantity is None:
                return "filled"
            return (
                "filled"
                if (position.opened_quantity or 0.0) >= expected_quantity - 1e-12
                else "partially_filled"
            )
        if position.status == "closed":
            if position.trade_id:
                self.trade_store.close_trade(
                    position.trade_id,
                    exit_price=allocation.price or position.entry_price,
                    exit_reason=allocation.reason,
                )
            return "closed"
        if allocation.action == "reduce":
            expected_quantity = self._metadata_float(position, "execution_quantity")
            if expected_quantity is None:
                expected_quantity = self._metadata_float(position, "close_quantity")
            if expected_quantity is not None and allocation.exchange_order_id:
                filled_quantity = sum(
                    item.quantity
                    for item in self.position_store.list_allocations(position.id)
                    if item.action == "reduce"
                    and item.exchange_order_id == allocation.exchange_order_id
                )
                if filled_quantity < expected_quantity - 1e-12:
                    self.position_store.update_status(
                        position.id,
                        status="reducing",
                        remaining_quantity=position.remaining_quantity,
                    )
                    return "partially_reduced"
        return "reduced"

    def _record_allocation_audit(
        self,
        position: LogicalPositionRecord,
        allocation: LogicalPositionAllocation,
        *,
        correlation_id: str,
        confirmation_source: FillSource,
        execution_status: str,
    ) -> None:
        self.audit_store.create(
            id=f"allocation:{allocation.id}",
            type="position.allocation_confirmed",
            source="execution_allocation",
            payload={
                "strategy_id": position.strategy_id,
                "correlation_id": correlation_id,
                "position_id": position.id,
                "trade_id": position.trade_id,
                "fill_id": allocation.id,
                "exchange_order_id": allocation.exchange_order_id,
                "action": allocation.action,
                "quantity": allocation.quantity,
                "price": allocation.price,
                "fee": allocation.fee,
                "confirmation_source": confirmation_source,
                "execution_status": execution_status,
                "opened_quantity": position.opened_quantity,
                "remaining_quantity": position.remaining_quantity,
                "average_entry_price": position.entry_price,
            },
            created_at=allocation.created_at,
        )

    def _update_correlated_decision(
        self,
        position: LogicalPositionRecord,
        allocation: LogicalPositionAllocation,
        *,
        correlation_id: str,
        execution_status: str,
    ) -> None:
        decisions = self.audit_store.list(
            event_type="strategy.action_decision",
            correlation_id=correlation_id,
            limit=1,
        )
        if not decisions:
            return
        decision = decisions[0]
        payload = dict(decision.payload)
        payload.update(
            {
                "execution_status": execution_status,
                "latest_fill_id": allocation.id,
                "filled_quantity": position.opened_quantity,
                "average_fill_price": position.entry_price,
            }
        )
        if execution_status in {"filled", "closed"}:
            payload["completed_at"] = datetime.now(timezone.utc).isoformat()
        self.audit_store.create(
            id=decision.id,
            type=decision.type,
            source=decision.source,
            payload=payload,
            created_at=decision.created_at,
        )

    @staticmethod
    def _metadata(position: LogicalPositionRecord) -> dict[str, Any]:
        try:
            value = json.loads(position.metadata_json or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _allocation_metadata(allocation: LogicalPositionAllocation) -> dict[str, Any]:
        try:
            value = json.loads(allocation.metadata_json or "{}")
        except json.JSONDecodeError:
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _metadata_string(position: LogicalPositionRecord, key: str) -> str:
        return str(ExecutionAllocationService._metadata(position).get(key) or "")

    @staticmethod
    def _metadata_float(position: LogicalPositionRecord, key: str) -> float | None:
        value = ExecutionAllocationService._metadata(position).get(key)
        try:
            return None if value is None else float(value)
        except (TypeError, ValueError):
            return None
