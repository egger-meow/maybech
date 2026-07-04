"""Explicitly import unexplained OKX exposure as one logical unit."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.trading.entry_control import ENTRY_EXECUTION_LOCK
from src.trading.audit_event_store import AuditEventStore
from src.trading.logical_position_store import (
    LogicalPositionCloseCondition,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.position_reconciliation import PositionReconciler
from src.trading.position_protection import PositionProtectionService
from src.trading.signal_engine import SignalExpressionEngine
from src.trading.strategy_runtime import resolve_self_symbol


class PositionImportConflict(RuntimeError):
    """Raised when there is no current unexplained exposure to import."""


@dataclass(frozen=True)
class PositionImportRequest:
    inst_id: str
    side: str
    close_conditions: list[dict[str, Any]]
    reason: str


class PositionImportService:
    def __init__(self, client: Any, store: LogicalPositionStore) -> None:
        self.client = client
        self.store = store
        self.reconciler = PositionReconciler()
        self.protection = PositionProtectionService(client, store)

    def import_unexplained(self, request: PositionImportRequest) -> LogicalPositionRecord:
        side = self.reconciler.normalize_side(request.side)
        if side == "unknown":
            raise ValueError("side must be long or short")
        if not request.inst_id:
            raise ValueError("inst_id is required")

        with ENTRY_EXECUTION_LOCK:
            report = self.reconciler.reconcile_account(
                logical_positions=self.store.list_active(),
                exchange_positions=self.client.get_positions(inst_type="SWAP"),
            )
            key = self.reconciler.position_key(request.inst_id, side)
            group = next((item for item in report.groups if item.key == key), None)
            if group is None or group.state != "under_allocated" or group.unexplained_quantity <= 0:
                raise PositionImportConflict(
                    f"no unexplained OKX exposure exists for {request.inst_id}:{side}"
                )
            if group.exchange_average_price is None or group.exchange_mark_price is None:
                raise ValueError("OKX position must include positive average and mark prices")

            position = LogicalPositionRecord(
                source="import",
                inst_id=request.inst_id,
                side=side,
                opened_quantity=group.unexplained_quantity,
                remaining_quantity=group.unexplained_quantity,
                entry_price=group.exchange_average_price,
                status="open",
                exchange_position_key=key,
                metadata_json=json.dumps(
                    {
                        "exchange_protection_verified": False,
                        "import_reason": request.reason,
                        "reconciliation_before_import": group.to_dict(),
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            conditions = self._conditions(
                position=position,
                specs=request.close_conditions,
                mark_price=group.exchange_mark_price,
            )
            self.store.create_with_close_conditions(position, conditions)
            return self.protection.protect(position.id)

    @staticmethod
    def _conditions(
        *,
        position: LogicalPositionRecord,
        specs: list[dict[str, Any]],
        mark_price: float,
    ) -> list[LogicalPositionCloseCondition]:
        engine = SignalExpressionEngine()
        conditions: list[LogicalPositionCloseCondition] = []
        has_stop = False
        expected_stop_type = "price_below" if position.side == "long" else "price_above"
        for spec in specs:
            expression = resolve_self_symbol(spec.get("expression") or {}, position.inst_id)
            validation = engine.validate(expression)
            if not validation.valid:
                raise ValueError("invalid close condition: " + "; ".join(validation.errors))
            enabled = bool(spec.get("enabled", True))
            purpose = str(spec.get("purpose") or "exit")
            if purpose == "stop_loss" and enabled:
                threshold = float(expression.get("value") or 0)
                if expression.get("type") != expected_stop_type:
                    raise ValueError("stop_loss is not side-consistent")
                if position.side == "long" and threshold >= mark_price:
                    raise ValueError("long stop_loss must be below the current mark price")
                if position.side == "short" and threshold <= mark_price:
                    raise ValueError("short stop_loss must be above the current mark price")
                has_stop = True
            conditions.append(
                LogicalPositionCloseCondition(
                    position_id=position.id,
                    purpose=purpose,
                    expression_json=json.dumps(expression, separators=(",", ":"), sort_keys=True),
                    enabled=enabled,
                    metadata_json=json.dumps(
                        spec.get("metadata") or {}, separators=(",", ":"), sort_keys=True
                    ),
                )
            )
        if not has_stop:
            raise ValueError("an enabled side-consistent stop_loss is required")
        return conditions


class PositionRecoveryService:
    """Represent clear exchange deltas without guessing external reductions."""

    def __init__(self, store: LogicalPositionStore) -> None:
        self.store = store
        self.reconciler = PositionReconciler()
        self.audit_store = AuditEventStore(store.db_path)

    def reconcile(self, exchange_positions: list[dict[str, Any]]) -> list[LogicalPositionRecord]:
        created: list[LogicalPositionRecord] = []
        with ENTRY_EXECUTION_LOCK:
            active = self.store.list_active()
            exchange_backed = [
                position for position in active if not self._is_dry_run(position)
            ]
            report = self.reconciler.reconcile_account(
                logical_positions=exchange_backed,
                exchange_positions=exchange_positions,
            )
            pending_keys = {
                self.reconciler.position_key(position.inst_id, position.side)
                for position in exchange_backed
                if position.status == "pending_open"
            }
            for group in report.groups:
                if group.state == "under_allocated" and group.unexplained_quantity > 0:
                    if group.key in pending_keys:
                        # OKX account state commonly arrives before the matching
                        # private/REST fill. Defer attribution until the pending
                        # open resolves; any genuine residual increase remains
                        # under-allocated on the next reconciliation pass.
                        continue
                    created.append(self._create_recovery(group))
                elif group.state in {"over_allocated", "no_exchange_position"}:
                    self._mark_manual_review(
                        group,
                        exchange_backed,
                        reason="OKX exposure decreased; logical-unit allocation is ambiguous",
                    )
                elif group.state == "balanced":
                    self._clear_pending_overlap_review(group, exchange_backed)
        return created

    def _clear_pending_overlap_review(
        self,
        group: Any,
        active: list[LogicalPositionRecord],
    ) -> None:
        obsolete_reason = "unexplained increase overlaps a pending Maybech open"
        for position in active:
            if self.reconciler.position_key(position.inst_id, position.side) != group.key:
                continue
            try:
                metadata = json.loads(position.metadata_json or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(metadata, dict) or metadata.get(
                "reconciliation_review_reason"
            ) != obsolete_reason:
                continue
            self.store.merge_metadata(position.id, {
                "requires_manual_review": False,
                "reconciliation_review_signature": "",
                "reconciliation_review_reason": "",
                "reconciliation_review_resolved_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            })

    @staticmethod
    def _is_dry_run(position: LogicalPositionRecord) -> bool:
        try:
            metadata = json.loads(position.metadata_json or "{}")
        except json.JSONDecodeError:
            return False
        return isinstance(metadata, dict) and metadata.get("dry_run") is True

    def _create_recovery(self, group: Any) -> LogicalPositionRecord:
        entry_price = group.exchange_average_price or group.exchange_mark_price or 0.0
        position = LogicalPositionRecord(
            source="recovery",
            inst_id=group.inst_id,
            side=group.side,
            opened_quantity=group.unexplained_quantity,
            remaining_quantity=group.unexplained_quantity,
            entry_price=entry_price,
            status="open",
            exchange_position_key=group.key,
            metadata_json=json.dumps(
                {
                    "exchange_protection_verified": False,
                    "requires_manual_review": True,
                    "recovery_reason": "clear unexplained OKX size increase",
                    "reconciliation_at_recovery": group.to_dict(),
                },
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
        with self.store.transaction() as connection:
            self.store.save(position)
            self.audit_store.create(
                type="position.recovered_from_exchange",
                source="account_reconciliation",
                payload={
                    "position_id": position.id,
                    "instrument": group.inst_id,
                    "side": group.side,
                    "quantity": group.unexplained_quantity,
                    "source": "recovery",
                    "requires_manual_review": True,
                },
                connection=connection,
            )
        return self.store.get(position.id) or position

    def _mark_manual_review(
        self,
        group: Any,
        active: list[LogicalPositionRecord],
        *,
        reason: str,
    ) -> None:
        members = [
            position
            for position in active
            if self.reconciler.position_key(position.inst_id, position.side) == group.key
        ]
        if not members:
            return
        signature = (
            f"{group.state}:{group.exchange_position_size}:"
            f"{group.logical_remaining}:{group.quantity_gap}"
        )
        changed: list[str] = []
        with self.store.transaction() as connection:
            for position in members:
                try:
                    metadata = json.loads(position.metadata_json or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                if not isinstance(metadata, dict):
                    metadata = {}
                if metadata.get("reconciliation_review_signature") == signature:
                    continue
                metadata.update(
                    {
                        "requires_manual_review": True,
                        "reconciliation_review_signature": signature,
                        "reconciliation_review_reason": reason,
                        "reconciliation_reviewed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                position.metadata_json = json.dumps(
                    metadata,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                self.store.save(position)
                changed.append(position.id)
            if changed:
                self.audit_store.create(
                    type="position.reconciliation_manual_review",
                    source="account_reconciliation",
                    payload={
                        "position_id": changed[0],
                        "position_ids": changed,
                        "instrument": group.inst_id,
                        "side": group.side,
                        "reason": reason,
                        "reconciliation": group.to_dict(),
                    },
                    connection=connection,
                )
