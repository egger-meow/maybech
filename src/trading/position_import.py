"""Explicitly import unexplained OKX exposure as one logical unit."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.trading.entry_control import ENTRY_EXECUTION_LOCK
from src.trading.logical_position_store import (
    LogicalPositionCloseCondition,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.position_reconciliation import PositionReconciler
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
            return position

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
