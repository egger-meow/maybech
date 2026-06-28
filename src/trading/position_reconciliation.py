"""Reconcile Maybech logical units against authoritative OKX net positions."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
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
    """Consistency view attached to one logical position unit."""

    position_id: str
    inst_id: str
    side: str
    state: ReconciliationState
    exchange_position_key: str = ""
    exchange_position_size: float | None = None
    group_logical_remaining: float | None = None
    group_quantity_gap: float | None = None
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExposureGroupReconciliation:
    key: str
    inst_id: str
    side: str
    state: ReconciliationState
    exchange_position_size: float | None
    logical_remaining: float | None
    quantity_gap: float | None
    unexplained_quantity: float
    exchange_average_price: float | None = None
    exchange_mark_price: float | None = None
    logical_position_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AccountExposureReconciliation:
    safe_for_entries: bool
    state: Literal["balanced", "mismatch", "invalid", "protection_required"]
    groups: list[ExposureGroupReconciliation]
    invalid_exchange_positions: list[str]
    invalid_logical_positions: list[str]
    unprotected_position_ids: list[str]
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PositionReconciler:
    """Conservatively compare logical quantities with OKX merged net rows."""

    ACTIVE_STATUSES = {"open", "reducing", "closing"}
    QUANTITY_TOLERANCE = 1e-12

    def reconcile(
        self,
        *,
        logical_positions: list[LogicalPositionRecord],
        exchange_positions: list[dict[str, Any]],
    ) -> dict[str, PositionReconciliation]:
        account = self.reconcile_account(
            logical_positions=logical_positions,
            exchange_positions=exchange_positions,
        )
        groups = {group.key: group for group in account.groups}
        result: dict[str, PositionReconciliation] = {}
        for position in logical_positions:
            if position.status not in self.ACTIVE_STATUSES:
                continue
            key = self.position_key(position.inst_id, position.side)
            group = groups.get(key)
            if group is None:
                continue
            result[position.id] = PositionReconciliation(
                position_id=position.id,
                inst_id=position.inst_id,
                side=self.normalize_side(position.side),
                state=group.state,
                exchange_position_key=(
                    group.key if group.exchange_position_size is not None else ""
                ),
                exchange_position_size=group.exchange_position_size,
                group_logical_remaining=group.logical_remaining,
                group_quantity_gap=group.quantity_gap,
                warnings=group.warnings,
            )
        return result

    def reconcile_account(
        self,
        *,
        logical_positions: list[LogicalPositionRecord],
        exchange_positions: list[dict[str, Any]],
    ) -> AccountExposureReconciliation:
        exchange_by_key, invalid = self._exchange_by_key(exchange_positions)
        logical_by_key: dict[str, list[LogicalPositionRecord]] = {}
        invalid_logical: list[str] = []
        unprotected: list[str] = []
        for position in logical_positions:
            if position.status not in self.ACTIVE_STATUSES:
                continue
            side = self.normalize_side(position.side)
            if not position.inst_id or side == "unknown":
                invalid_logical.append(position.id)
                continue
            if position.source in {"import", "recovery"}:
                try:
                    metadata = json.loads(position.metadata_json or "{}")
                except json.JSONDecodeError:
                    metadata = {}
                if not isinstance(metadata, dict) or not metadata.get(
                    "exchange_protection_verified", False
                ):
                    unprotected.append(position.id)
            logical_by_key.setdefault(
                self.position_key(position.inst_id, side), []
            ).append(position)

        groups: list[ExposureGroupReconciliation] = []
        for key in sorted(set(exchange_by_key) | set(logical_by_key)):
            positions = logical_by_key.get(key, [])
            exchange = exchange_by_key.get(key)
            exchange_size = None if exchange is None else exchange["size"]
            logical_remaining = self._group_remaining(positions) if positions else 0.0
            state, gap, warnings = self._group_state(
                exchange_size=exchange_size,
                group_remaining=logical_remaining,
            )
            inst_id, side = key.rsplit(":", 1)
            groups.append(
                ExposureGroupReconciliation(
                    key=key,
                    inst_id=inst_id,
                    side=side,
                    state=state,
                    exchange_position_size=exchange_size,
                    logical_remaining=logical_remaining,
                    quantity_gap=gap,
                    unexplained_quantity=(
                        max(0.0, gap or 0.0) if state == "under_allocated" else 0.0
                    ),
                    exchange_average_price=(
                        None if exchange is None else exchange["average_price"]
                    ),
                    exchange_mark_price=(
                        None if exchange is None else exchange["mark_price"]
                    ),
                    logical_position_ids=[position.id for position in positions],
                    warnings=warnings,
                )
            )

        if invalid or invalid_logical:
            account_state: Literal[
                "balanced", "mismatch", "invalid", "protection_required"
            ] = "invalid"
        elif any(group.state != "balanced" for group in groups):
            account_state = "mismatch"
        elif unprotected:
            account_state = "protection_required"
        else:
            account_state = "balanced"
        return AccountExposureReconciliation(
            safe_for_entries=account_state == "balanced",
            state=account_state,
            groups=groups,
            invalid_exchange_positions=invalid,
            invalid_logical_positions=invalid_logical,
            unprotected_position_ids=unprotected,
            checked_at=datetime.now(timezone.utc).isoformat(),
        )

    def _group_state(
        self,
        *,
        exchange_size: float | None,
        group_remaining: float | None,
    ) -> tuple[ReconciliationState, float | None, list[str]]:
        if exchange_size is None:
            return "no_exchange_position", None, ["no matching OKX net position"]
        if group_remaining is None:
            return (
                "unknown_quantity",
                None,
                ["one or more logical units has unknown remaining quantity"],
            )
        gap = round(exchange_size - group_remaining, 12)
        if abs(gap) <= self.QUANTITY_TOLERANCE:
            return "balanced", 0.0, []
        if gap > 0:
            return (
                "under_allocated",
                gap,
                ["OKX net position is larger than tracked logical remaining quantity"],
            )
        return (
            "over_allocated",
            gap,
            ["Tracked logical remaining quantity is larger than OKX net position"],
        )

    @staticmethod
    def _group_remaining(positions: list[LogicalPositionRecord]) -> float | None:
        total = 0.0
        for position in positions:
            quantity = position.remaining_quantity
            if quantity is None:
                quantity = position.opened_quantity
            if quantity is None:
                return None
            total += quantity
        return round(total, 12)

    def _exchange_by_key(
        self,
        exchange_positions: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], list[str]]:
        grouped: dict[str, dict[str, Any]] = {}
        invalid: list[str] = []
        for index, position in enumerate(exchange_positions):
            inst_id = str(position.get("inst_id") or position.get("instId") or "")
            raw_size = position.get("position")
            if raw_size in (None, ""):
                raw_size = position.get("pos")
            if raw_size in (None, ""):
                raw_size = position.get("sz")
            size = self._signed_float(raw_size)
            if not inst_id or size is None:
                invalid.append(f"exchange position {index} has invalid instrument or size")
                continue
            if abs(size) <= self.QUANTITY_TOLERANCE:
                continue
            raw_side = position.get("pos_side") or position.get("posSide") or position.get("side")
            side = self.normalize_side(raw_side)
            if str(raw_side or "").lower() == "net" or side == "unknown":
                side = "long" if size > 0 else "short"
            size = abs(size)
            key = self.position_key(inst_id, side)
            average_price = self._positive_float(
                position.get("avg_price") or position.get("avgPx")
            )
            mark_price = self._positive_float(
                position.get("mark_price") or position.get("markPx")
            )
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = {
                    "size": size,
                    "average_price": average_price,
                    "mark_price": mark_price,
                }
                continue
            previous_size = existing["size"]
            total_size = previous_size + size
            existing["average_price"] = self._weighted_price(
                existing["average_price"], previous_size, average_price, size
            )
            existing["mark_price"] = mark_price or existing["mark_price"]
            existing["size"] = total_size
        return grouped, invalid

    @staticmethod
    def _weighted_price(
        previous_price: float | None,
        previous_size: float,
        price: float | None,
        size: float,
    ) -> float | None:
        if previous_price is None:
            return price
        if price is None:
            return previous_price
        return ((previous_price * previous_size) + (price * size)) / (
            previous_size + size
        )

    @staticmethod
    def position_key(inst_id: str, side: object) -> str:
        return f"{inst_id}:{PositionReconciler.normalize_side(side)}"

    @staticmethod
    def normalize_side(side: object) -> str:
        normalized = str(side or "").lower()
        if normalized in {"long", "buy"}:
            return "long"
        if normalized in {"short", "sell"}:
            return "short"
        return "unknown"

    @staticmethod
    def _signed_float(value: Any) -> float | None:
        try:
            if value in (None, ""):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _positive_float(value: Any) -> float | None:
        parsed = PositionReconciler._signed_float(value)
        return parsed if parsed is not None and parsed > 0 else None
