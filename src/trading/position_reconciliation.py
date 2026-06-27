"""Reconcile Maybech logical position units against OKX net positions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from src.trading.logical_position_store import LogicalPositionRecord


ReconciliationState = Literal[
    "balanced",
    "under_allocated",
    "over_allocated",
    "no_exchange_position",
    "unknown_quantity",
]


@dataclass(frozen=True)
class PositionReconciliation:
    """Consistency view for one logical position unit."""

    position_id: str
    inst_id: str
    side: str
    state: ReconciliationState
    exchange_position_key: str = ""
    exchange_position_size: float | None = None
    group_logical_remaining: float | None = None
    group_quantity_gap: float | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


class PositionReconciler:
    """Conservative reconciliation between logical units and OKX merged rows.

    The reconciler reports consistency state. It does not infer which logical
    unit was reduced when OKX only exposes one merged position and no execution
    event identifies the unit.
    """

    ACTIVE_STATUSES = {"open", "reducing", "closing"}

    def reconcile(
        self,
        *,
        logical_positions: list[LogicalPositionRecord],
        exchange_positions: list[dict[str, Any]],
    ) -> dict[str, PositionReconciliation]:
        exchange_by_key = self._exchange_by_key(exchange_positions)
        result: dict[str, PositionReconciliation] = {}
        logical_by_key: dict[str, list[LogicalPositionRecord]] = {}

        for position in logical_positions:
            if position.status not in self.ACTIVE_STATUSES:
                continue
            key = self._position_key(position.inst_id, position.side)
            logical_by_key.setdefault(key, []).append(position)

        for key, positions in logical_by_key.items():
            exchange = exchange_by_key.get(key)
            exchange_size = None if exchange is None else exchange["size"]
            group_remaining = self._group_remaining(positions)
            state, gap, warnings = self._group_state(
                exchange_size=exchange_size,
                group_remaining=group_remaining,
                positions=positions,
            )
            exchange_position_key = key if exchange is not None else ""

            for position in positions:
                result[position.id] = PositionReconciliation(
                    position_id=position.id,
                    inst_id=position.inst_id,
                    side=self._normalize_side(position.side),
                    state=state,
                    exchange_position_key=exchange_position_key,
                    exchange_position_size=exchange_size,
                    group_logical_remaining=group_remaining,
                    group_quantity_gap=gap,
                    warnings=warnings,
                )

        return result

    def _group_state(
        self,
        *,
        exchange_size: float | None,
        group_remaining: float | None,
        positions: list[LogicalPositionRecord],
    ) -> tuple[ReconciliationState, float | None, list[str]]:
        warnings: list[str] = []
        if exchange_size is None:
            return "no_exchange_position", None, ["no matching OKX net position"]
        if group_remaining is None:
            return "unknown_quantity", None, ["one or more logical units has unknown remaining quantity"]

        gap = round(exchange_size - group_remaining, 12)
        if abs(gap) <= 1e-12:
            return "balanced", 0.0, warnings
        if gap > 0:
            warnings.append("OKX net position is larger than tracked logical remaining quantity")
            return "under_allocated", gap, warnings
        warnings.append("Tracked logical remaining quantity is larger than OKX net position")
        return "over_allocated", gap, warnings

    def _group_remaining(self, positions: list[LogicalPositionRecord]) -> float | None:
        total = 0.0
        for position in positions:
            quantity = position.remaining_quantity
            if quantity is None:
                quantity = position.opened_quantity
            if quantity is None:
                return None
            total += quantity
        return round(total, 12)

    def _exchange_by_key(self, exchange_positions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for position in exchange_positions:
            inst_id = str(position.get("inst_id") or position.get("instId") or "")
            side = self._normalize_side(position.get("pos_side") or position.get("posSide") or position.get("side"))
            if not inst_id or side == "unknown":
                continue
            size = self._as_float(position.get("position") or position.get("pos") or position.get("sz"))
            if size is None:
                continue
            key = self._position_key(inst_id, side)
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = {"size": size}
            else:
                existing["size"] += size
        return grouped

    def _position_key(self, inst_id: str, side: object) -> str:
        return f"{inst_id}:{self._normalize_side(side)}"

    def _normalize_side(self, side: object) -> str:
        normalized = str(side or "").lower()
        if normalized in {"long", "buy"}:
            return "long"
        if normalized in {"short", "sell"}:
            return "short"
        return "unknown"

    def _as_float(self, value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return abs(float(value))
        except (TypeError, ValueError):
            return None
