"""Attach and verify exchange stops for imported or recovered logical units."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from src.trading.entry_control import ENTRY_EXECUTION_LOCK
from src.trading.instrument_constraints import InstrumentConstraints
from src.trading.logical_position_store import LogicalPositionRecord, LogicalPositionStore
from src.trading.position_reconciliation import PositionReconciler


class PositionProtectionError(RuntimeError):
    """Raised when active exchange protection cannot be proven."""


class PositionProtectionService:
    """Create one independently sized OKX stop for one logical position unit."""

    PROTECTABLE_SOURCES = {"import", "recovery"}

    def __init__(self, client: Any, store: LogicalPositionStore) -> None:
        self.client = client
        self.store = store
        self.reconciler = PositionReconciler()

    def protect(self, position_id: str) -> LogicalPositionRecord:
        with ENTRY_EXECUTION_LOCK:
            position = self.store.get(position_id)
            if position is None:
                raise PositionProtectionError("logical position not found")
            try:
                return self._protect(position)
            except Exception as exc:
                self.store.merge_metadata(
                    position.id,
                    {
                        "exchange_protection_verified": False,
                        "exchange_protection_error": str(exc),
                        "exchange_protection_checked_at": self._now(),
                    },
                )
                if isinstance(exc, PositionProtectionError):
                    raise
                raise PositionProtectionError(str(exc)) from exc

    def _protect(self, position: LogicalPositionRecord) -> LogicalPositionRecord:
        if position.source not in self.PROTECTABLE_SOURCES:
            raise PositionProtectionError(
                "standalone protection is only for imported or recovered positions"
            )
        if position.status not in {"open", "reducing", "closing"}:
            raise PositionProtectionError("logical position is not active")
        quantity = position.remaining_quantity or position.opened_quantity or 0.0
        if quantity <= 0:
            raise PositionProtectionError("logical position has no remaining quantity")

        report = self.reconciler.reconcile_account(
            logical_positions=self.store.list_active(),
            exchange_positions=self.client.get_positions(inst_type="SWAP"),
        )
        key = self.reconciler.position_key(position.inst_id, position.side)
        group = next((item for item in report.groups if item.key == key), None)
        if group is None or group.state != "balanced":
            raise PositionProtectionError(
                "logical quantity must reconcile with OKX before protection is attached"
            )

        constraints = self._constraints(position.inst_id)
        size = constraints.normalize_size(quantity)
        stop = constraints.normalize_price(self._stop_price(position))
        algo_client_id = self._algo_client_id(position.id)
        pending = self.client.get_pending_algo_orders(inst_id=position.inst_id)
        order = self._find_pending(pending, algo_client_id)
        if order is not None:
            try:
                proof = self._verify_pending(
                    order,
                    position=position,
                    quantity=size,
                    stop=stop,
                    algo_client_id=algo_client_id,
                )
            except PositionProtectionError:
                self._verify_identity(
                    order,
                    position=position,
                    algo_client_id=algo_client_id,
                )
                self.client.amend_position_stop(
                    inst_id=position.inst_id,
                    algo_id=str(order["algoId"]),
                    sz=size,
                    stop_trigger_px=stop,
                    confirm=True,
                )
                pending = self.client.get_pending_algo_orders(inst_id=position.inst_id)
                order = self._find_pending(pending, algo_client_id)
                proof = self._verify_pending(
                    order,
                    position=position,
                    quantity=size,
                    stop=stop,
                    algo_client_id=algo_client_id,
                )
        else:
            accepted = self.client.place_position_stop(
                inst_id=position.inst_id,
                position_side=position.side,
                sz=size,
                stop_trigger_px=stop,
                algo_client_order_id=algo_client_id,
                confirm=True,
            )
            self.store.merge_metadata(
                position.id,
                {
                    "exchange_protection_verified": False,
                    "exchange_protection": {
                        "algo_id": str(accepted["algoId"]),
                        "algo_client_order_id": algo_client_id,
                        "quantity": size,
                        "stop_loss": stop,
                    },
                },
            )
            pending = self.client.get_pending_algo_orders(inst_id=position.inst_id)
            order = self._find_pending(pending, algo_client_id)
            proof = self._verify_pending(
                order,
                position=position,
                quantity=size,
                stop=stop,
                algo_client_id=algo_client_id,
            )
        updated = self.store.merge_metadata(
            position.id,
            {
                "exchange_protection_verified": True,
                "exchange_protection_error": "",
                "exchange_protection_checked_at": self._now(),
                "exchange_protection": proof,
            },
        )
        if updated is None:
            raise PositionProtectionError("logical position disappeared during protection")
        return updated

    def _constraints(self, inst_id: str) -> InstrumentConstraints:
        payloads = self.client.get_instruments(inst_type="SWAP", inst_id=inst_id)
        if len(payloads) != 1:
            raise PositionProtectionError(
                f"expected one OKX instrument for {inst_id}, got {len(payloads)}"
            )
        constraints = InstrumentConstraints.from_okx(payloads[0])
        if constraints.inst_id != inst_id:
            raise PositionProtectionError("OKX instrument response does not match position")
        constraints.validate_tradable()
        return constraints

    def _stop_price(self, position: LogicalPositionRecord) -> float:
        expected_type = "price_below" if position.side == "long" else "price_above"
        stops: list[float] = []
        for condition in self.store.list_close_conditions(position.id, enabled=True):
            expression = condition.expression
            if condition.purpose != "stop_loss" or expression.get("type") != expected_type:
                continue
            if expression.get("symbol") != position.inst_id:
                continue
            try:
                value = float(expression.get("value"))
            except (TypeError, ValueError):
                continue
            if value > 0:
                stops.append(value)
        if len(stops) != 1:
            raise PositionProtectionError(
                "exactly one enabled side-consistent price stop_loss is required"
            )
        return stops[0]

    @staticmethod
    def _find_pending(orders: list[dict[str, Any]], algo_client_id: str) -> dict[str, Any] | None:
        matches = [
            order
            for order in orders
            if str(order.get("algoClOrdId") or "") == algo_client_id
        ]
        if len(matches) > 1:
            raise PositionProtectionError("OKX returned duplicate protection algo orders")
        return matches[0] if matches else None

    @staticmethod
    def _verify_pending(
        order: dict[str, Any] | None,
        *,
        position: LogicalPositionRecord,
        quantity: str,
        stop: str,
        algo_client_id: str,
    ) -> dict[str, Any]:
        if order is None:
            raise PositionProtectionError("OKX does not report the stop as pending")
        expected_side = "sell" if position.side == "long" else "buy"
        checks = {
            "algo client ID": str(order.get("algoClOrdId") or "") == algo_client_id,
            "instrument": str(order.get("instId") or "") == position.inst_id,
            "side": str(order.get("side") or "").lower() == expected_side,
            "order type": str(order.get("ordType") or "").lower() == "conditional",
            "state": str(order.get("state") or "").lower() == "live",
            "position side": str(order.get("posSide") or "").lower() == "net",
            "reduce only": str(order.get("reduceOnly") or "").lower() == "true",
            "size": PositionProtectionService._same_decimal(order.get("sz"), quantity),
            "stop trigger": PositionProtectionService._same_decimal(
                order.get("slTriggerPx"), stop
            ),
            "stop order price": str(order.get("slOrdPx") or "") == "-1",
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise PositionProtectionError(
                "OKX pending stop verification failed: " + ", ".join(failed)
            )
        algo_id = str(order.get("algoId") or "")
        if not algo_id:
            raise PositionProtectionError("OKX pending stop is missing algoId")
        return {
            "type": "standalone_stop",
            "algo_id": algo_id,
            "algo_client_order_id": algo_client_id,
            "quantity": quantity,
            "stop_loss": stop,
            "trigger_price_type": "last",
            "verified_at": PositionProtectionService._now(),
        }

    @staticmethod
    def _verify_identity(
        order: dict[str, Any],
        *,
        position: LogicalPositionRecord,
        algo_client_id: str,
    ) -> None:
        expected_side = "sell" if position.side == "long" else "buy"
        checks = {
            "algo client ID": str(order.get("algoClOrdId") or "") == algo_client_id,
            "instrument": str(order.get("instId") or "") == position.inst_id,
            "side": str(order.get("side") or "").lower() == expected_side,
            "order type": str(order.get("ordType") or "").lower() == "conditional",
            "state": str(order.get("state") or "").lower() == "live",
            "position side": str(order.get("posSide") or "").lower() == "net",
            "reduce only": str(order.get("reduceOnly") or "").lower() == "true",
            "algo ID": bool(str(order.get("algoId") or "")),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise PositionProtectionError(
                "existing OKX stop cannot be safely amended: " + ", ".join(failed)
            )

    @staticmethod
    def _same_decimal(left: object, right: object) -> bool:
        try:
            return Decimal(str(left)) == Decimal(str(right))
        except (InvalidOperation, ValueError):
            return False

    @staticmethod
    def _algo_client_id(position_id: str) -> str:
        digest = hashlib.sha256(position_id.encode("utf-8")).hexdigest()
        return f"mbp{digest[:29]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
