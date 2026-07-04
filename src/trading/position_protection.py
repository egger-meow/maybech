"""Attach and verify exchange stops for imported or recovered logical units."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, NoReturn
from uuid import uuid4

from src.exchange.client import disable_entry_order_placement
from src.trading.account_risk import AccountRiskStore
from src.trading.audit_event_store import AuditEventStore
from src.trading.entry_control import ENTRY_EXECUTION_LOCK
from src.trading.instrument_constraints import InstrumentConstraints
from src.trading.logical_position_store import (
    LogicalPositionProtection,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.position_reconciliation import PositionReconciler
from src.trading.position_rule_model import calculate_break_even_target


class PositionProtectionError(RuntimeError):
    """Raised when active exchange protection cannot be proven."""


class PositionProtectionService:
    """Create one independently sized OKX stop for one logical position unit."""

    def __init__(
        self,
        client: Any,
        store: LogicalPositionStore,
        audit_store: AuditEventStore | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.audit_store = audit_store or AuditEventStore(store.db_path)
        self.reconciler = PositionReconciler()

    def amend_stop_condition(
        self,
        position_id: str,
        condition_id: str,
        *,
        expression: dict[str, Any],
        reason: str,
        condition_metadata: dict[str, Any] | None = None,
        intent_metadata: dict[str, Any] | None = None,
        expected_position_updated_at: str | None = None,
        expected_condition_updated_at: str | None = None,
    ) -> LogicalPositionRecord:
        """Amend one owned exchange stop before publishing its new rule value."""
        with ENTRY_EXECUTION_LOCK:
            position = self.store.get(position_id)
            protection = self.store.get_protection(position_id)
            condition = self.store.get_close_condition(position_id, condition_id)
            if position is None:
                raise PositionProtectionError("logical position not found")
            if position.status not in {"open", "reducing", "closing"}:
                raise PositionProtectionError("logical position is not active")
            if protection is None:
                raise PositionProtectionError("logical position has no owned protection")
            if protection.status != "active":
                raise PositionProtectionError(f"protection is {protection.status}")
            if condition is None:
                raise PositionProtectionError("close condition not found")
            if expected_position_updated_at is not None and position.updated_at != expected_position_updated_at:
                raise PositionProtectionError("logical position changed since stop review")
            if expected_condition_updated_at is not None and condition.updated_at != expected_condition_updated_at:
                raise PositionProtectionError("stop condition changed since stop review")
            if condition.purpose != "stop_loss" or not condition.enabled:
                raise PositionProtectionError(
                    "condition must be the enabled stop_loss owned by protection"
                )
            enabled_stops = [
                item
                for item in self.store.list_close_conditions(position.id, enabled=True)
                if item.purpose == "stop_loss"
            ]
            if len(enabled_stops) != 1 or enabled_stops[0].id != condition.id:
                raise PositionProtectionError(
                    "protection requires exactly one owned enabled stop_loss condition"
                )
            remaining = position.remaining_quantity or position.opened_quantity or 0.0
            if remaining <= 0 or not self._same_decimal(
                protection.quantity,
                remaining,
            ):
                raise PositionProtectionError(
                    "owned protection quantity does not match remaining logical quantity"
                )

            normalized_expression, stop = self._normalize_stop_expression(
                position,
                expression,
            )
            try:
                constraints = self._constraints(position.inst_id)
                size = constraints.normalize_size(remaining)
                normalized_stop = constraints.normalize_price(stop)
            except ValueError as exc:
                raise PositionProtectionError(str(exc)) from exc
            pending = self._pending_orders(position.inst_id)
            order = self._find_pending(pending, protection.algo_client_order_id)
            self._verify_for_kind(
                order,
                position=position,
                quantity=size,
                stop=str(protection.stop_loss),
                algo_client_id=protection.algo_client_order_id,
                kind=protection.kind,
            )

            correlation_id = uuid4().hex
            requested_at = self._now()
            amend_intent = {
                **(intent_metadata or {}),
                "correlation_id": correlation_id,
                "condition_id": condition_id,
                "old_stop_loss": protection.stop_loss,
                "target_stop_loss": normalized_stop,
                "reason": reason,
                "requested_at": requested_at,
            }
            self.audit_store.create(
                type="position.protection_stop_amend_requested",
                source="position_protection",
                payload={
                    "position_id": position.id,
                    "trade_id": position.trade_id,
                    "strategy_id": position.strategy_id,
                    **amend_intent,
                },
            )
            amending = self.store.update_protection(
                position.id,
                status="amending",
                metadata={"stop_amend": amend_intent},
            )
            if amending is None:
                raise PositionProtectionError(
                    "could not persist protective stop amend intent"
                )

            try:
                self.client.amend_position_stop(
                    inst_id=position.inst_id,
                    algo_id=protection.algo_id,
                    sz=size,
                    stop_trigger_px=normalized_stop,
                    confirm=True,
                )
                proof = self._verify_amended_stop(
                    position=position,
                    protection=protection,
                    quantity=size,
                    stop=normalized_stop,
                )
            except Exception as exc:
                proof = self._resolve_amend_error(
                    position=position,
                    protection=protection,
                    quantity=size,
                    target_stop=normalized_stop,
                    error=exc,
                    amend_intent=amend_intent,
                )

            normalized_expression = {
                **normalized_expression,
                "value": float(Decimal(normalized_stop)),
            }
            updated_condition = self.store.update_close_condition(
                position.id,
                condition.id,
                expression=normalized_expression,
                metadata=(
                    {**condition.metadata, **condition_metadata}
                    if condition_metadata is not None
                    else None
                ),
            )
            if updated_condition is None:
                self._fail_amend(
                    position,
                    amend_intent,
                    "close condition disappeared after exchange amend",
                )

            completed_at = self._now()
            self.store.update_protection(
                position.id,
                status="active",
                stop_loss=float(Decimal(normalized_stop)),
                metadata={
                    "stop_amend": {
                        **amend_intent,
                        "status": "completed",
                        "completed_at": completed_at,
                    },
                    "verified_at": proof["verified_at"],
                },
            )
            updated = self.store.merge_metadata(
                position.id,
                {
                    "exchange_protection_verified": True,
                    "exchange_protection_error": "",
                    "exchange_protection_checked_at": completed_at,
                    "exchange_protection": proof,
                },
            )
            if updated is None:
                self._fail_amend(
                    position,
                    amend_intent,
                    "logical position disappeared after exchange amend",
                )
            try:
                self.audit_store.create(
                    type="position.protection_stop_amended",
                    source="position_protection",
                    payload={
                        "position_id": position.id,
                        "trade_id": position.trade_id,
                        "strategy_id": position.strategy_id,
                        **amend_intent,
                        "completed_at": completed_at,
                        "proof": proof,
                    },
                )
            except Exception as exc:
                self._fail_amend(
                    position,
                    amend_intent,
                    f"protective stop completion audit failed: {exc}",
                )
            return updated

    def move_to_break_even(
        self,
        position_id: str,
        condition_id: str,
        *,
        lock_in_pct: Decimal,
        reason: str,
        expected_position_updated_at: str | None = None,
        expected_condition_updated_at: str | None = None,
        entry_fee_rate: Decimal = Decimal("0.0005"),
        exit_fee_rate: Decimal = Decimal("0.0005"),
        slippage_rate: Decimal = Decimal("0.0005"),
    ) -> LogicalPositionRecord:
        """Move an owned stop to entry or a side-consistent protected profit."""
        with ENTRY_EXECUTION_LOCK:
            position = self.store.get(position_id)
            if position is None:
                raise PositionProtectionError("logical position not found")
            if position.side not in {"long", "short"}:
                raise PositionProtectionError("logical position side must be long or short")
            try:
                lock_in = Decimal(str(lock_in_pct))
                entry = Decimal(str(position.entry_price))
            except (InvalidOperation, ValueError) as exc:
                raise PositionProtectionError("invalid break-even price input") from exc
            if not lock_in.is_finite() or lock_in < 0 or lock_in > Decimal("0.05"):
                raise PositionProtectionError(
                    "lock_in_pct must be between 0 and 0.05"
                )
            if not entry.is_finite() or entry <= 0:
                raise PositionProtectionError("logical position entry price must be positive")

            try:
                raw_target, cost_evidence = calculate_break_even_target(
                    entry_price=entry,
                    side=position.side,
                    entry_fee_rate=entry_fee_rate,
                    exit_fee_rate=exit_fee_rate,
                    slippage_rate=slippage_rate,
                    lock_in_pct=lock_in,
                )
                constraints = self._constraints(position.inst_id)
                target = constraints.normalize_break_even_price(
                    raw_target,
                    position_side=position.side,
                )
                tickers = self.client.get_ticker(inst_id=position.inst_id)
            except (PositionProtectionError, ValueError) as exc:
                raise PositionProtectionError(str(exc)) from exc
            if len(tickers) != 1:
                raise PositionProtectionError(
                    f"expected one ticker for {position.inst_id}, got {len(tickers)}"
                )
            try:
                current = Decimal(str(tickers[0].get("last")))
            except (InvalidOperation, ValueError) as exc:
                raise PositionProtectionError("ticker last price is invalid") from exc
            if not current.is_finite() or current <= 0:
                raise PositionProtectionError("ticker last price must be positive")
            target_decimal = Decimal(target)
            favorable = (
                current > target_decimal
                if position.side == "long"
                else current < target_decimal
            )
            if not favorable:
                raise PositionProtectionError(
                    "current price has not moved beyond the requested break-even stop"
                )

            applied_at = self._now()
            break_even = {
                "status": "applied",
                "entry_price": position.entry_price,
                "target_stop": target,
                "current_price": str(current),
                "lock_in_pct": str(lock_in),
                "costs": cost_evidence,
                "applied_at": applied_at,
            }
            expression = {
                "type": "price_below" if position.side == "long" else "price_above",
                "symbol": position.inst_id,
                "value": float(target_decimal),
            }
            return self.amend_stop_condition(
                position.id,
                condition_id,
                expression=expression,
                reason=reason,
                condition_metadata={"break_even": break_even},
                intent_metadata={"operation": "break_even", "break_even": break_even},
                expected_position_updated_at=expected_position_updated_at,
                expected_condition_updated_at=expected_condition_updated_at,
            )

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

    def adopt_recovered_position(
        self,
        position_id: str,
        *,
        stop_loss: Decimal,
        reason: str,
    ) -> LogicalPositionRecord:
        """Add a first stop, prove it at OKX, then clear recovery review."""
        with ENTRY_EXECUTION_LOCK:
            position = self.store.get(position_id)
            if position is None:
                raise PositionProtectionError("logical position not found")
            if position.source != "recovery":
                raise PositionProtectionError("only recovered positions use adoption")
            try:
                metadata = json.loads(position.metadata_json or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            if metadata.get("reconciliation_review_reason"):
                raise PositionProtectionError(
                    "ambiguous external reduction must be reconciled before adoption"
                )
            if metadata.get("requires_manual_review") is not True:
                raise PositionProtectionError("recovered position does not require adoption")
            try:
                stop = Decimal(str(stop_loss))
            except (InvalidOperation, ValueError) as exc:
                raise PositionProtectionError("stop_loss must be numeric") from exc
            if not stop.is_finite() or stop <= 0:
                raise PositionProtectionError("stop_loss must be positive")
            tickers = self.client.get_ticker(inst_id=position.inst_id)
            if len(tickers) != 1:
                raise PositionProtectionError(
                    f"expected one ticker for {position.inst_id}, got {len(tickers)}"
                )
            try:
                current = Decimal(str(tickers[0].get("last")))
            except (InvalidOperation, ValueError) as exc:
                raise PositionProtectionError("ticker last price is invalid") from exc
            if not current.is_finite() or current <= 0:
                raise PositionProtectionError("ticker last price must be positive")
            if position.side == "long" and stop >= current:
                raise PositionProtectionError(
                    "long stop_loss must be below the current market price"
                )
            if position.side == "short" and stop <= current:
                raise PositionProtectionError(
                    "short stop_loss must be above the current market price"
                )
            expected_type = "price_below" if position.side == "long" else "price_above"
            enabled_stops = [
                item
                for item in self.store.list_close_conditions(position.id, enabled=True)
                if item.purpose == "stop_loss"
            ]
            if len(enabled_stops) > 1:
                raise PositionProtectionError(
                    "recovered position has multiple enabled stop_loss conditions"
                )
            expression = {
                "type": expected_type,
                "symbol": position.inst_id,
                "value": float(stop),
            }
            if enabled_stops:
                updated_condition = self.store.update_close_condition(
                    position.id,
                    enabled_stops[0].id,
                    expression=expression,
                )
                if updated_condition is None:
                    raise PositionProtectionError("could not update recovery stop_loss")
            else:
                created = self.store.create_close_condition(
                    position_id=position.id,
                    purpose="stop_loss",
                    expression=expression,
                    enabled=True,
                    metadata={"source": "recovery_adoption"},
                )
                if created is None:
                    raise PositionProtectionError("could not create recovery stop_loss")
            self.audit_store.create(
                type="position.recovery_adoption_requested",
                source="position_protection",
                payload={
                    "position_id": position.id,
                    "instrument": position.inst_id,
                    "side": position.side,
                    "quantity": position.remaining_quantity,
                    "stop_loss": str(stop),
                    "reason": reason,
                },
            )
            protected = self._protect(position)
            adopted_at = self._now()
            self.audit_store.create(
                type="position.recovery_adopted",
                source="position_protection",
                payload={
                    "position_id": position.id,
                    "instrument": position.inst_id,
                    "side": position.side,
                    "quantity": position.remaining_quantity,
                    "stop_loss": str(stop),
                    "reason": reason,
                    "adopted_at": adopted_at,
                    "protection": json.loads(protected.metadata_json).get(
                        "exchange_protection", {}
                    ),
                },
            )
            adopted = self.store.merge_metadata(
                position.id,
                {
                    "requires_manual_review": False,
                    "recovery_adopted_at": adopted_at,
                    "recovery_adoption_reason": reason,
                },
            )
            if adopted is None:
                raise PositionProtectionError(
                    "logical position disappeared after recovery protection"
                )
            return adopted

    def _protect(self, position: LogicalPositionRecord) -> LogicalPositionRecord:
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
        existing_protection = self.store.get_protection(position.id)
        if (
            existing_protection is not None
            and existing_protection.status == "active"
            and self._stop_is_looser(
                side=position.side,
                candidate=stop,
                confirmed=existing_protection.stop_loss,
            )
        ):
            # The independently confirmed per-position stop is authoritative.
            # Reconciliation/restart may repair quantity or exchange drift, but
            # it must never restore a looser strategy-template value.
            stop = constraints.normalize_price(existing_protection.stop_loss)
            enabled_stops = [
                item
                for item in self.store.list_close_conditions(position.id, enabled=True)
                if item.purpose == "stop_loss"
            ]
            if len(enabled_stops) != 1:
                raise PositionProtectionError(
                    "confirmed protection requires exactly one enabled stop_loss condition"
                )
            confirmed_expression = {
                **enabled_stops[0].expression,
                "symbol": position.inst_id,
                "value": float(Decimal(stop)),
            }
            if self.store.update_close_condition(
                position.id,
                enabled_stops[0].id,
                expression=confirmed_expression,
                metadata={
                    **enabled_stops[0].metadata,
                    "protection_regression_prevented": {
                        "rejected_stop": self._stop_price(position),
                        "confirmed_stop": stop,
                        "repaired_at": self._now(),
                    },
                },
            ) is None:
                raise PositionProtectionError(
                    "could not restore confirmed stop rule during protection repair"
                )
        algo_client_id = (
            existing_protection.algo_client_order_id
            if existing_protection is not None
            and existing_protection.status not in {"canceled", "exhausted"}
            else self._algo_client_id(position.id)
        )
        pending = self._pending_orders(position.inst_id)
        order = self._find_pending(pending, algo_client_id)
        if order is not None:
            try:
                proof = self._verify_for_kind(
                    order,
                    position=position,
                    quantity=size,
                    stop=stop,
                    algo_client_id=algo_client_id,
                    kind=(
                        existing_protection.kind
                        if existing_protection is not None
                        else "standalone_stop"
                    ),
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
                pending = self._pending_orders(position.inst_id)
                order = self._find_pending(pending, algo_client_id)
                proof = self._verify_for_kind(
                    order,
                    position=position,
                    quantity=size,
                    stop=stop,
                    algo_client_id=algo_client_id,
                    kind=(
                        existing_protection.kind
                        if existing_protection is not None
                        else "standalone_stop"
                    ),
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
            try:
                pending = self._pending_orders(position.inst_id)
            except Exception as exc:
                raise PositionProtectionError(
                    f"could not inspect active protective stop: {exc}"
                ) from exc
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
        kind = (
            existing_protection.kind
            if existing_protection is not None
            and existing_protection.algo_id == str(proof["algo_id"])
            else "standalone_stop"
        )
        self.store.save_protection(
            LogicalPositionProtection(
                position_id=position.id,
                kind=kind,
                status="active",
                algo_id=str(proof["algo_id"]),
                algo_client_order_id=str(proof["algo_client_order_id"]),
                quantity=float(proof["quantity"]),
                stop_loss=float(proof["stop_loss"]),
                metadata_json=json.dumps(
                    {"verified_at": proof["verified_at"]},
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
        )
        return updated

    def cancel_for_close(self, position_id: str, *, reason: str) -> bool:
        """Cancel and prove removal of the unit's stop before a software close."""
        with ENTRY_EXECUTION_LOCK:
            position = self.store.get(position_id)
            protection = self.store.get_protection(position_id)
            if position is None:
                raise PositionProtectionError("logical position not found")
            if protection is None or protection.status in {"canceled", "exhausted"}:
                return False
            if protection.status != "active":
                raise PositionProtectionError(
                    f"protection is {protection.status}; software close is unsafe"
                )
            try:
                pending = self._pending_orders(position.inst_id)
            except Exception as exc:
                raise PositionProtectionError(
                    f"could not inspect active protective stop: {exc}"
                ) from exc
            order = self._find_pending(pending, protection.algo_client_order_id)
            self._verify_for_kind(
                order,
                position=position,
                quantity=str(protection.quantity),
                stop=str(protection.stop_loss),
                algo_client_id=protection.algo_client_order_id,
                kind=protection.kind,
            )
            self.store.update_protection(
                position.id,
                status="canceling",
                metadata={"cancel_reason": reason, "cancel_requested_at": self._now()},
            )
            try:
                self.client.cancel_position_stop(
                    inst_id=position.inst_id,
                    algo_id=protection.algo_id,
                    confirm=True,
                )
            except Exception as exc:
                self.store.update_protection(
                    position.id,
                    status="failed",
                    metadata={
                        "cancel_error": str(exc),
                        "cancel_outcome": "unknown",
                    },
                )
                raise PositionProtectionError(
                    f"protective stop cancellation outcome is unknown: {exc}"
                ) from exc
            for _ in range(5):
                try:
                    pending = self._pending_orders(position.inst_id)
                except Exception as exc:
                    self.store.update_protection(
                        position.id,
                        status="failed",
                        metadata={
                            "cancel_error": str(exc),
                            "cancel_outcome": "unknown",
                        },
                    )
                    raise PositionProtectionError(
                        f"protective stop cancellation outcome is unknown: {exc}"
                    ) from exc
                if self._find_pending(
                    pending,
                    protection.algo_client_order_id,
                ) is None:
                    self.store.update_protection(
                        position.id,
                        status="canceled",
                        metadata={"canceled_at": self._now()},
                    )
                    self.store.merge_metadata(
                        position.id,
                        {
                            "exchange_protection_verified": False,
                            "exchange_protection_error": "canceled for software close",
                            "protection_canceled_for_close": True,
                        },
                    )
                    return True
                time.sleep(0.1)
            self.store.update_protection(
                position.id,
                status="active",
                metadata={"cancel_error": "OKX still reports protection as pending"},
            )
            raise PositionProtectionError(
                "OKX still reports protection as pending after cancellation"
            )

    def verify_active(self, position_id: str) -> LogicalPositionProtection:
        """Prove the persisted protection still exists exactly on OKX."""
        with ENTRY_EXECUTION_LOCK:
            return self._verify_active(position_id)

    def _verify_active(self, position_id: str) -> LogicalPositionProtection:
        position = self.store.get(position_id)
        protection = self.store.get_protection(position_id)
        if position is None:
            raise PositionProtectionError("logical position not found")
        if protection is None:
            raise PositionProtectionError("logical position has no owned protection")
        if protection.status != "active":
            raise PositionProtectionError(f"protection is {protection.status}")
        remaining = position.remaining_quantity or position.opened_quantity or 0.0
        if remaining <= 0 or not self._same_decimal(
            protection.quantity,
            remaining,
        ):
            raise PositionProtectionError(
                "owned protection quantity does not match remaining logical quantity"
            )
        pending = self._pending_orders(position.inst_id)
        order = self._find_pending(pending, protection.algo_client_order_id)
        self._verify_for_kind(
            order,
            position=position,
            quantity=str(protection.quantity),
            stop=str(protection.stop_loss),
            algo_client_id=protection.algo_client_order_id,
            kind=protection.kind,
        )
        self._verify_persisted_stop_claims(position, protection)
        return protection

    def _verify_persisted_stop_claims(
        self,
        position: LogicalPositionRecord,
        protection: LogicalPositionProtection,
    ) -> None:
        stops = [
            item
            for item in self.store.list_close_conditions(position.id, enabled=True)
            if item.purpose == "stop_loss"
        ]
        if len(stops) != 1:
            raise PositionProtectionError(
                "confirmed protection requires exactly one enabled stop_loss condition"
            )
        try:
            rule_stop = Decimal(str(stops[0].expression.get("value")))
            confirmed_stop = Decimal(str(protection.stop_loss))
        except (InvalidOperation, ValueError) as exc:
            raise PositionProtectionError("persisted stop evidence is invalid") from exc
        if rule_stop != confirmed_stop:
            raise PositionProtectionError(
                "persisted stop rule does not match confirmed exchange protection"
            )

        claimed_stops: list[tuple[str, object]] = []
        break_even = stops[0].metadata.get("break_even")
        if isinstance(break_even, dict) and break_even.get("status") == "applied":
            claimed_stops.append(("break-even", break_even.get("target_stop")))
        for condition in self.store.list_close_conditions(position.id):
            if condition.purpose == "break_even":
                state = condition.metadata.get("break_even_state")
                if isinstance(state, dict) and state.get("status") == "applied":
                    claimed_stops.append(("break-even", state.get("target_stop")))
            elif condition.purpose == "trailing":
                state = condition.metadata.get("trailing_state")
                if isinstance(state, dict) and state.get("last_applied_stop") is not None:
                    claimed_stops.append(("trailing", state.get("last_applied_stop")))
        for label, value in claimed_stops:
            try:
                claimed = Decimal(str(value))
            except (InvalidOperation, ValueError) as exc:
                raise PositionProtectionError(
                    f"persisted {label} stop evidence is invalid"
                ) from exc
            if self._stop_is_looser(
                side=position.side,
                candidate=confirmed_stop,
                confirmed=claimed,
            ):
                raise PositionProtectionError(
                    f"confirmed exchange protection is looser than applied {label} state"
                )

    @staticmethod
    def _stop_is_looser(*, side: str, candidate: object, confirmed: object) -> bool:
        try:
            candidate_price = Decimal(str(candidate))
            confirmed_price = Decimal(str(confirmed))
        except (InvalidOperation, ValueError) as exc:
            raise PositionProtectionError("protective stop price is invalid") from exc
        if not candidate_price.is_finite() or not confirmed_price.is_finite():
            raise PositionProtectionError("protective stop price is invalid")
        if side == "long":
            return candidate_price < confirmed_price
        if side == "short":
            return candidate_price > confirmed_price
        raise PositionProtectionError("logical position side must be long or short")

    def _verify_amended_stop(
        self,
        *,
        position: LogicalPositionRecord,
        protection: LogicalPositionProtection,
        quantity: str,
        stop: str,
    ) -> dict[str, Any]:
        pending = self._pending_orders(position.inst_id)
        order = self._find_pending(pending, protection.algo_client_order_id)
        return self._verify_for_kind(
            order,
            position=position,
            quantity=quantity,
            stop=stop,
            algo_client_id=protection.algo_client_order_id,
            kind=protection.kind,
        )

    def _resolve_amend_error(
        self,
        *,
        position: LogicalPositionRecord,
        protection: LogicalPositionProtection,
        quantity: str,
        target_stop: str,
        error: Exception,
        amend_intent: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            pending = self._pending_orders(position.inst_id)
            order = self._find_pending(pending, protection.algo_client_order_id)
        except Exception as query_error:
            self._fail_amend(
                position,
                amend_intent,
                f"amend outcome unknown: {error}; verification failed: {query_error}",
            )

        try:
            return self._verify_for_kind(
                order,
                position=position,
                quantity=quantity,
                stop=target_stop,
                algo_client_id=protection.algo_client_order_id,
                kind=protection.kind,
            )
        except PositionProtectionError:
            try:
                self._verify_for_kind(
                    order,
                    position=position,
                    quantity=quantity,
                    stop=str(protection.stop_loss),
                    algo_client_id=protection.algo_client_order_id,
                    kind=protection.kind,
                )
            except PositionProtectionError as verification_error:
                self._fail_amend(
                    position,
                    amend_intent,
                    f"amend outcome unknown: {error}; {verification_error}",
                )

        self.store.update_protection(
            position.id,
            status="active",
            metadata={
                "stop_amend": {
                    **amend_intent,
                    "status": "not_applied",
                    "error": str(error),
                    "completed_at": self._now(),
                }
            },
        )
        self.audit_store.create(
            type="position.protection_stop_amend_failed",
            source="position_protection",
            payload={
                "position_id": position.id,
                "trade_id": position.trade_id,
                "strategy_id": position.strategy_id,
                **amend_intent,
                "outcome": "not_applied",
                "error": str(error),
            },
        )
        raise PositionProtectionError(
            f"protective stop amend was not applied: {error}"
        ) from error

    def _fail_amend(
        self,
        position: LogicalPositionRecord,
        amend_intent: dict[str, Any],
        error: str,
    ) -> NoReturn:
        disable_entry_order_placement()
        persistence_errors: list[str] = []
        try:
            self.store.update_protection(
                position.id,
                status="failed",
                metadata={
                    "stop_amend": {
                        **amend_intent,
                        "status": "unknown",
                        "error": error,
                        "completed_at": self._now(),
                    }
                },
            )
        except Exception as exc:
            persistence_errors.append(f"protection failure state: {exc}")
        try:
            AccountRiskStore(self.store.db_path).set_entries_enabled(False)
        except Exception as exc:
            persistence_errors.append(f"durable entry disable: {exc}")
        final_error = error
        if persistence_errors:
            final_error += "; " + "; ".join(persistence_errors)
        try:
            self.audit_store.create(
                type="position.protection_stop_amend_failed",
                source="position_protection",
                payload={
                    "position_id": position.id,
                    "trade_id": position.trade_id,
                    "strategy_id": position.strategy_id,
                    **amend_intent,
                    "outcome": "unknown",
                    "error": final_error,
                },
            )
        except Exception:
            pass
        raise PositionProtectionError(final_error)

    @staticmethod
    def _normalize_stop_expression(
        position: LogicalPositionRecord,
        expression: dict[str, Any],
    ) -> tuple[dict[str, Any], Decimal]:
        if position.side not in {"long", "short"}:
            raise PositionProtectionError("logical position side must be long or short")
        expected_type = "price_below" if position.side == "long" else "price_above"
        if expression.get("type") != expected_type:
            raise PositionProtectionError(
                f"{position.side} stop_loss must use {expected_type}"
            )
        symbol = str(expression.get("symbol") or "")
        if symbol not in {"self", position.inst_id}:
            raise PositionProtectionError(
                "stop_loss symbol must be self or the position instrument"
            )
        try:
            stop = Decimal(str(expression.get("value")))
        except (InvalidOperation, ValueError) as exc:
            raise PositionProtectionError("stop_loss value must be numeric") from exc
        if not stop.is_finite() or stop <= 0:
            raise PositionProtectionError("stop_loss value must be positive")
        return {**expression, "symbol": position.inst_id}, stop

    def _pending_orders(self, inst_id: str) -> list[dict[str, Any]]:
        try:
            orders = [
                *self.client.get_pending_algo_orders(
                    inst_id=inst_id,
                    ord_type="conditional",
                ),
                *self.client.get_pending_algo_orders(inst_id=inst_id, ord_type="oco"),
            ]
        except TypeError:
            orders = self.client.get_pending_algo_orders(inst_id=inst_id)
        unique: dict[str, dict[str, Any]] = {}
        for order in orders:
            key = str(order.get("algoId") or order.get("algoClOrdId") or id(order))
            unique[key] = order
        return list(unique.values())

    @staticmethod
    def _verify_for_kind(
        order: dict[str, Any] | None,
        *,
        position: LogicalPositionRecord,
        quantity: str,
        stop: str,
        algo_client_id: str,
        kind: str,
    ) -> dict[str, Any]:
        if kind == "standalone_stop":
            return PositionProtectionService._verify_pending(
                order,
                position=position,
                quantity=quantity,
                stop=stop,
                algo_client_id=algo_client_id,
            )
        if order is None:
            raise PositionProtectionError("OKX does not report the stop as pending")
        checks = {
            "algo client ID": str(order.get("algoClOrdId") or "") == algo_client_id,
            "instrument": str(order.get("instId") or "") == position.inst_id,
            "state": str(order.get("state") or "").lower() == "live",
            "size": PositionProtectionService._same_decimal(order.get("sz"), quantity),
            "stop trigger": PositionProtectionService._same_decimal(
                order.get("slTriggerPx"), stop
            ),
            "stop order price": str(order.get("slOrdPx") or "") == "-1",
            "algo ID": bool(str(order.get("algoId") or "")),
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise PositionProtectionError(
                "OKX attached protection verification failed: " + ", ".join(failed)
            )
        return {
            "type": "attached_stop",
            "algo_id": str(order["algoId"]),
            "algo_client_order_id": algo_client_id,
            "quantity": quantity,
            "stop_loss": stop,
            "trigger_price_type": str(order.get("slTriggerPxType") or "last"),
            "verified_at": PositionProtectionService._now(),
        }

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
