"""Operator entry control that never disables reduce-only position closes."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.exchange.client import (
    OKXClient,
    disable_entry_order_placement,
    enable_entry_order_placement,
    entry_order_placement_enabled,
)
from src.trading.account_risk import AccountRiskStore
from src.trading.audit_event_store import AuditEventStore
from src.trading.logical_position_store import LogicalPositionStore


ENTRY_EXECUTION_LOCK = threading.RLock()
_TERMINAL_STATES = {"canceled", "cancelled", "filled", "rejected", "mmp_canceled"}


@dataclass(frozen=True)
class EntryControlResult:
    entries_enabled: bool
    process_entry_enabled: bool
    persisted: bool
    pending_entries: int = 0
    cancellations_requested: int = 0
    already_requested: int = 0
    already_terminal: int = 0
    unresolved: int = 0
    errors: list[str] = field(default_factory=list)
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EntryControlManager:
    """Persist operator intent and cancel only Maybech pending entry orders."""

    def __init__(
        self,
        *,
        client: OKXClient | None = None,
        risk_store: AccountRiskStore | None = None,
        position_store: LogicalPositionStore | None = None,
        audit_store: AuditEventStore | None = None,
    ) -> None:
        self.risk_store = risk_store or AccountRiskStore()
        self.position_store = position_store or LogicalPositionStore(
            self.risk_store.db_path
        )
        self.audit_store = audit_store or AuditEventStore(self.risk_store.db_path)
        self.client = client

    def status(self) -> EntryControlResult:
        return EntryControlResult(
            entries_enabled=self.risk_store.entries_enabled(),
            process_entry_enabled=entry_order_placement_enabled(),
            persisted=True,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def enable_entries(self) -> EntryControlResult:
        with ENTRY_EXECUTION_LOCK:
            limits = self.risk_store.get()
            if limits is None or not limits.enabled:
                raise ValueError("Enabled account risk limits are required")
            saved = self.risk_store.set_entries_enabled(True)
            if saved is None:
                raise RuntimeError("Account risk limits disappeared during entry enable")
            try:
                try:
                    enable_entry_order_placement()
                except PermissionError:
                    # Persisted intent is applied by the next successful live startup.
                    pass
                result = EntryControlResult(
                    entries_enabled=True,
                    process_entry_enabled=entry_order_placement_enabled(),
                    persisted=True,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
                self._audit("entry_control.enabled", result)
                return result
            except Exception:
                disable_entry_order_placement()
                self.risk_store.set_entries_enabled(False)
                raise

    def kill_entries(self) -> EntryControlResult:
        with ENTRY_EXECUTION_LOCK:
            disable_entry_order_placement()
            errors: list[str] = []
            try:
                self.risk_store.set_entries_enabled(False)
                persisted = True
            except Exception as exc:
                persisted = False
                errors.append(f"failed to persist disabled state: {exc}")

            positions = [
                position
                for position in self.position_store.list_pending_executions()
                if position.status == "pending_open"
            ]
            client = self.client
            requested = 0
            already_requested = 0
            already_terminal = 0
            unresolved = 0
            for position in positions:
                order_id = position.exchange_order_id
                if order_id and self.position_store.is_order_cancel_requested(
                    position.id,
                    exchange_order_id=order_id,
                ):
                    already_requested += 1
                    continue
                if not order_id and position.client_order_id:
                    if client is None:
                        client = OKXClient()
                    try:
                        orders = client.get_order(
                            position.inst_id,
                            client_order_id=position.client_order_id,
                        )
                    except Exception as exc:
                        unresolved += 1
                        errors.append(f"{position.id}: order lookup failed: {exc}")
                        continue
                    if len(orders) != 1:
                        unresolved += 1
                        errors.append(
                            f"{position.id}: expected one order for client ID, got {len(orders)}"
                        )
                        continue
                    order = orders[0]
                    order_id = str(order.get("ordId") or "")
                    order_state = str(order.get("state") or "").lower()
                    if order_state in _TERMINAL_STATES:
                        already_terminal += 1
                        continue
                    if order_id:
                        linked = self.position_store.link_exchange_order(
                            position.id,
                            client_order_id=position.client_order_id,
                            exchange_order_id=order_id,
                            metadata={"execution_status": "kill_switch_order_recovered"},
                        )
                        if linked is not None:
                            position = linked
                if not order_id:
                    unresolved += 1
                    errors.append(f"{position.id}: no exchange order ID available")
                    continue
                if client is None:
                    client = OKXClient()
                try:
                    response = client.cancel_order(position.inst_id, order_id)
                except Exception as exc:
                    unresolved += 1
                    errors.append(f"{position.id}: cancellation failed: {exc}")
                    continue
                cancel_code = str(response.get("sCode") or "") if response else ""
                if not response or cancel_code not in {"", "0"}:
                    unresolved += 1
                    errors.append(
                        f"{position.id}: cancellation rejected"
                        + (f" (sCode={cancel_code})" if cancel_code else "")
                    )
                    continue
                if self.position_store.mark_order_cancel_requested(
                    position.id,
                    exchange_order_id=order_id,
                ):
                    requested += 1

            result = EntryControlResult(
                entries_enabled=False,
                process_entry_enabled=entry_order_placement_enabled(),
                persisted=persisted,
                pending_entries=len(positions),
                cancellations_requested=requested,
                already_requested=already_requested,
                already_terminal=already_terminal,
                unresolved=unresolved,
                errors=errors,
                updated_at=datetime.now(timezone.utc).isoformat(),
            )
            try:
                self._audit("entry_control.killed", result)
            except Exception as exc:
                errors.append(f"failed to persist kill audit: {exc}")
            return result

    def _audit(self, event_type: str, result: EntryControlResult) -> None:
        self.audit_store.create(
            type=event_type,
            source="entry_control",
            payload=result.to_dict(),
            created_at=result.updated_at,
        )
