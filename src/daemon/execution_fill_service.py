"""Poll authenticated OKX fills and allocate matching logical positions."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from src.config.settings import settings
from src.daemon.service import DaemonService
from src.exchange.client import OKXClient
from src.exchange.fills import normalize_okx_fill
from src.exchange.websocket import OKXPrivateOrderStream
from src.exchange.client import disable_entry_order_placement
from src.trading.account_risk import AccountRiskStore
from src.trading.logical_position_store import AllocationConflictError
from src.trading.execution_cursor_store import ExecutionCursorStore
from src.trading.execution_health import evaluate_execution_health
from src.trading.execution_allocation import ExecutionAllocationService
from src.trading.position_protection import (
    PositionProtectionError,
    PositionProtectionService,
)
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


class ExecutionFillService(DaemonService):
    """Provide polling catch-up for fills missed before or between websocket sessions."""

    name = "execution_fills"
    interval = 1.0
    TERMINAL_ORDER_STATES = {"canceled", "cancelled", "rejected", "mmp_canceled"}
    ACTIVE_ORDER_STATES = {"live", "partially_filled"}
    FILL_STREAM_ID = "okx:fills-history:SWAP"

    def __init__(
        self,
        *,
        client: OKXClient | None = None,
        allocator: ExecutionAllocationService | None = None,
        cursor_store: ExecutionCursorStore | None = None,
        stale_after_seconds: float = 300.0,
        missing_fill_alert_after: int = 3,
        max_pages_per_tick: int = 5,
        private_order_stream: OKXPrivateOrderStream | None = None,
        enable_private_stream: bool = False,
        rest_poll_interval: float = 0.0,
        protection_service: PositionProtectionService | None = None,
        allow_order_mutations: bool = True,
        protection_gap_alert_seconds: float = 5.0,
    ) -> None:
        super().__init__()
        self.client = client
        self.allocator = allocator or ExecutionAllocationService()
        self.cursor_store = cursor_store or ExecutionCursorStore(
            self.allocator.trade_store.db_path
        )
        self.stale_after_seconds = stale_after_seconds
        self.missing_fill_alert_after = max(1, missing_fill_alert_after)
        self.max_pages_per_tick = max(1, min(5, max_pages_per_tick))
        self.private_order_stream = private_order_stream
        self.enable_private_stream = enable_private_stream
        self.rest_poll_interval = max(0.0, rest_poll_interval)
        self._next_rest_poll = 0.0
        self._last_rest_status: dict[str, Any] = {}
        self._reconciled_dropped_events = 0
        self._last_health_failure_at = ""
        self._last_health_failure_reasons: list[str] = []
        self.protection_service = protection_service
        self.allow_order_mutations = allow_order_mutations
        self.protection_gap_alert_seconds = max(0.0, protection_gap_alert_seconds)

    def setup(self) -> None:
        if self.client is None:
            self.client = OKXClient()
        if self.protection_service is None:
            self.protection_service = PositionProtectionService(
                self.client,
                self.allocator.position_store,
            )
        if self.enable_private_stream:
            if self.private_order_stream is None:
                self.private_order_stream = OKXPrivateOrderStream(
                    api_key=settings.OKX_API_KEY,
                    api_secret=settings.OKX_API_SECRET,
                    passphrase=settings.OKX_PASSPHRASE,
                    flag=settings.OKX_FLAG,
                )
            self.private_order_stream.start()
        initial_status = self._empty_status()
        self._apply_private_stream_status(initial_status)
        if self.runtime is not None:
            self.runtime.set_value("execution.fills.status", initial_status)
        logger.info("ExecutionFillService setup complete.")

    def tick(self) -> None:
        if self.client is None:
            raise RuntimeError("ExecutionFillService is not set up")
        status = self._empty_status()
        catchup_error: Exception | None = None
        now = time.monotonic()
        self._apply_private_stream_status(status)
        dropped_before_rest = int(status["websocket_dropped_events"])
        if now >= self._next_rest_poll:
            try:
                self._catch_up_fills(status)
            except Exception as exc:
                catchup_error = exc
                status["cursor_errors"] += 1
                status["cursor_error"] = str(exc)
                logger.error("OKX fill catch-up failed: %s", exc)

            self._reconcile_pending_orders(status)
            self._reconcile_owned_protections(status)
            cursor = self.cursor_store.get(self.FILL_STREAM_ID)
            status["cursor_in_progress"] = cursor.in_progress
            status["high_water_bill_id"] = cursor.high_water_id
            status["next_after_bill_id"] = cursor.next_after_id
            self._last_rest_status = {
                key: value
                for key, value in status.items()
                if not key.startswith("websocket_") and key != "updated_at"
            }
            if catchup_error is None:
                self._reconciled_dropped_events = dropped_before_rest
            self._next_rest_poll = now + self.rest_poll_interval
        else:
            status.update(self._last_rest_status)
            status["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._drain_private_order_events(status)
        status["websocket_drops_pending_catchup"] = max(
            0,
            int(status["websocket_dropped_events"])
            - self._reconciled_dropped_events,
        )
        if status["websocket_drops_pending_catchup"]:
            self._next_rest_poll = 0.0
        self._track_protection_gaps(status)
        status = evaluate_execution_health(status)
        if not status["healthy"]:
            self._last_health_failure_at = str(status["updated_at"])
            self._last_health_failure_reasons = list(status["health_reasons"])
        status["last_health_failure_at"] = self._last_health_failure_at
        status["last_health_failure_reasons"] = self._last_health_failure_reasons
        if self.allow_order_mutations and not status["healthy"]:
            self._disable_entries_for_execution_error()

        if self.runtime is not None:
            self.runtime.set_value("execution.fills.status", status)
        self.publish_event("execution.fills_polled", status)
        if catchup_error is not None:
            raise catchup_error

    @staticmethod
    def _empty_status() -> dict[str, Any]:
        return {
            "fetched": 0,
            "applied": 0,
            "idempotent": 0,
            "unmatched": 0,
            "invalid": 0,
            "conflicts": 0,
            "orders_checked": 0,
            "terminal_recovered": 0,
            "stale_cancel_requested": 0,
            "filled_awaiting_allocation": 0,
            "missing_fill_alerts": 0,
            "order_errors": 0,
            "client_orders_linked": 0,
            "missing_client_orders_recovered": 0,
            "protection_triggers_linked": 0,
            "protections_checked": 0,
            "protection_rearmed": 0,
            "protection_errors": 0,
            "unprotected_positions": 0,
            "oldest_protection_gap_seconds": 0.0,
            "protection_gap_alerts": 0,
            "rule_materializations": 0,
            "rule_materialization_errors": 0,
            "pages_fetched": 0,
            "caught_up": False,
            "cursor_in_progress": False,
            "history_exhausted": False,
            "high_water_bill_id": "",
            "next_after_bill_id": "",
            "cursor_errors": 0,
            "cursor_error": "",
            "websocket_enabled": False,
            "websocket_connected": False,
            "websocket_events_received": 0,
            "websocket_events_processed": 0,
            "websocket_fills_applied": 0,
            "websocket_terminal_recovered": 0,
            "websocket_reconnects": 0,
            "websocket_dropped_events": 0,
            "websocket_drops_pending_catchup": 0,
            "websocket_last_message_at": "",
            "websocket_last_error": "",
            "service_available": True,
            "last_health_failure_at": "",
            "last_health_failure_reasons": [],
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    def _drain_private_order_events(self, status: dict[str, Any]) -> None:
        stream = self.private_order_stream
        if stream is None:
            return
        for order in stream.drain(limit=1000):
            status["websocket_events_processed"] += 1
            applied_before = status["applied"]
            if str(order.get("tradeId") or ""):
                websocket_fill = {
                    **order,
                    "fee": order.get("fillFee"),
                    "feeCcy": order.get("fillFeeCcy"),
                    "ts": order.get("fillTime") or order.get("uTime"),
                }
                self._ingest_fills([websocket_fill], status)
                status["websocket_fills_applied"] += (
                    status["applied"] - applied_before
                )

            order_id = str(order.get("ordId") or "")
            client_order_id = str(order.get("clOrdId") or "")
            position = self.allocator.position_store.get_by_exchange_order_id(order_id)
            if position is None and client_order_id:
                position = self.allocator.position_store.get_by_client_order_id(
                    client_order_id
                )
                if position is not None and order_id:
                    linked = self.allocator.position_store.link_exchange_order(
                        position.id,
                        client_order_id=client_order_id,
                        exchange_order_id=order_id,
                        metadata={"execution_status": "exchange_order_recovered_from_ws"},
                    )
                    if linked is not None:
                        position = linked
                        status["client_orders_linked"] += 1

            order_state = str(order.get("state") or "").lower()
            if (
                position is not None
                and order_id
                and order_state in self.TERMINAL_ORDER_STATES
            ):
                recovered_before = status["terminal_recovered"]
                self._recover_terminal_order(
                    position=position,
                    order_id=order_id,
                    order_state=order_state,
                    status=status,
                    source="websocket",
                )
                status["websocket_terminal_recovered"] += (
                    status["terminal_recovered"] - recovered_before
                )
        self._apply_private_stream_status(status)

    def _apply_private_stream_status(self, status: dict[str, Any]) -> None:
        stream = self.private_order_stream
        if stream is None:
            return
        snapshot = stream.status_dict()
        status.update(
            {
                "websocket_enabled": bool(snapshot["enabled"]),
                "websocket_connected": bool(snapshot["connected"]),
                "websocket_events_received": int(snapshot["events_received"]),
                "websocket_reconnects": int(snapshot["reconnects"]),
                "websocket_dropped_events": int(snapshot["dropped_events"]),
                "websocket_last_message_at": str(snapshot["last_message_at"]),
                "websocket_last_error": str(snapshot["last_error"]),
            }
        )

    def _catch_up_fills(self, status: dict[str, Any]) -> None:
        if self.client is None:
            raise RuntimeError("ExecutionFillService is not set up")
        cursor = self.cursor_store.get(self.FILL_STREAM_ID)
        previous_high_water = cursor.high_water_id
        pending_high_water = cursor.pending_high_water_id
        after = cursor.next_after_id if cursor.in_progress else ""

        for _ in range(self.max_pages_per_tick):
            raw_fills = self.client.get_fills_history(
                inst_type="SWAP",
                limit="100",
                after=after,
            )
            status["pages_fetched"] += 1
            status["fetched"] += len(raw_fills)
            if not raw_fills:
                if pending_high_water:
                    self.cursor_store.complete(
                        self.FILL_STREAM_ID,
                        high_water_id=pending_high_water,
                    )
                status["caught_up"] = True
                status["history_exhausted"] = True
                return

            bill_ids = [str(item.get("billId") or "") for item in raw_fills]
            if any(not bill_id for bill_id in bill_ids):
                raise ValueError("OKX fill history page is missing billId")
            if not pending_high_water:
                pending_high_water = bill_ids[0]

            self._ingest_fills(raw_fills, status)
            reached_boundary = bool(
                previous_high_water and previous_high_water in bill_ids
            )
            exhausted = len(raw_fills) < 100
            if reached_boundary or exhausted:
                self.cursor_store.complete(
                    self.FILL_STREAM_ID,
                    high_water_id=pending_high_water,
                )
                status["caught_up"] = True
                status["history_exhausted"] = exhausted and not reached_boundary
                return

            next_after = bill_ids[-1]
            if next_after == after:
                raise RuntimeError(f"OKX fill pagination did not advance after {after!r}")
            self.cursor_store.checkpoint(
                self.FILL_STREAM_ID,
                pending_high_water_id=pending_high_water,
                next_after_id=next_after,
            )
            after = next_after

    def _ingest_fills(
        self,
        raw_fills: list[dict[str, Any]],
        status: dict[str, Any],
    ) -> None:
        for raw_fill in raw_fills:
            self._link_protection_trigger(raw_fill, status)
            try:
                fill = normalize_okx_fill(raw_fill)
            except ValueError as exc:
                status["invalid"] += 1
                logger.warning("Ignoring invalid OKX fill: %s", exc)
                self._record_fill_rejection(raw_fill, str(exc), category="invalid")
                continue
            try:
                result = self.allocator.ingest(fill)
            except LookupError:
                status["unmatched"] += 1
                continue
            except AllocationConflictError as exc:
                status["conflicts"] += 1
                logger.error("Conflicting OKX fill %s: %s", fill.fill_id, exc)
                self.publish_event(
                    "execution.fill_conflict",
                    {"fill_id": fill.fill_id, "error": str(exc)},
                )
                self._record_fill_rejection(raw_fill, str(exc), category="conflict")
                continue
            except ValueError as exc:
                status["invalid"] += 1
                logger.warning("Rejected OKX fill %s: %s", fill.fill_id, exc)
                self._record_fill_rejection(raw_fill, str(exc), category="rejected")
                continue
            if result.allocation.action == "open":
                self._apply_pending_rule_materializations(result.position.id, status)
            elif result.allocation.action in {"reduce", "close"}:
                # A terminal private-stream fill must restore protection in this
                # same tick. Waiting for the slower REST reconciliation loop
                # leaves the confirmed remainder unprotected.
                self._rearm_protection_if_needed(
                    result.position,
                    order_id=fill.exchange_order_id,
                    status=status,
                )
            if result.idempotent:
                status["idempotent"] += 1
            else:
                status["applied"] += 1
                self.publish_event(
                    "execution.fill_applied",
                    {
                        "fill_id": fill.fill_id,
                        "exchange_order_id": fill.exchange_order_id,
                        "position_id": result.position.id,
                        "trade_id": result.position.trade_id,
                        "execution_status": result.execution_status,
                    },
                )

    def _apply_pending_rule_materializations(
        self,
        position_id: str,
        status: dict[str, Any],
    ) -> None:
        position = self.allocator.position_store.get(position_id)
        if position is None:
            return
        for condition in self.allocator.position_store.list_close_conditions(position.id):
            pending = condition.metadata.get("pending_materialization")
            if not isinstance(pending, dict) or pending.get("status") != "pending_exchange_amend":
                continue
            expression = pending.get("expression")
            materialized_metadata = pending.get("metadata")
            if not isinstance(expression, dict) or not isinstance(materialized_metadata, dict):
                status["rule_materialization_errors"] += 1
                continue
            if not self.allow_order_mutations or self.protection_service is None:
                status["rule_materialization_errors"] += 1
                AccountRiskStore(self.allocator.trade_store.db_path).set_entries_enabled(False)
                disable_entry_order_placement()
                continue
            try:
                self.protection_service.amend_stop_condition(
                    position.id,
                    condition.id,
                    expression=expression,
                    reason="materialize stop from confirmed average entry",
                    condition_metadata={
                        **materialized_metadata,
                        "pending_materialization": {
                            **pending,
                            "status": "applied",
                            "applied_at": datetime.now(timezone.utc).isoformat(),
                        },
                    },
                    intent_metadata={
                        "operation": "confirmed_entry_materialization",
                        "entry_fill_id": pending.get("entry_fill_id"),
                    },
                    expected_position_updated_at=position.updated_at,
                    expected_condition_updated_at=condition.updated_at,
                )
                self.allocator.position_store.merge_metadata(position.id, {
                    "requires_manual_review": False,
                    "protection_materialization_pending": False,
                })
                status["rule_materializations"] += 1
            except PositionProtectionError as exc:
                status["rule_materialization_errors"] += 1
                status["protection_errors"] += 1
                AccountRiskStore(self.allocator.trade_store.db_path).set_entries_enabled(False)
                disable_entry_order_placement()
                logger.error(
                    "Confirmed-entry stop materialization failed for %s: %s",
                    position.id,
                    exc,
                )

    def _link_protection_trigger(
        self,
        raw_order_or_fill: dict[str, Any],
        status: dict[str, Any],
    ) -> None:
        order_id = str(raw_order_or_fill.get("ordId") or "")
        if not order_id or self.allocator.position_store.get_by_exchange_order_id(order_id):
            return
        payload = raw_order_or_fill
        algo_id = str(payload.get("algoId") or "")
        algo_client_id = str(
            payload.get("algoClOrdId") or payload.get("attachAlgoClOrdId") or ""
        )
        if not algo_id and not algo_client_id and self.client is not None:
            inst_id = str(payload.get("instId") or "")
            if inst_id:
                try:
                    orders = self.client.get_order(inst_id, order_id=order_id)
                except Exception as exc:
                    status["protection_errors"] += 1
                    logger.warning(
                        "Failed to inspect unmatched order %s for algo ownership: %s",
                        order_id,
                        exc,
                    )
                    return
                if len(orders) == 1:
                    payload = orders[0]
                    algo_id = str(payload.get("algoId") or "")
                    algo_client_id = str(payload.get("algoClOrdId") or "")
        protection = self.allocator.position_store.get_protection_by_algo(
            algo_id=algo_id,
            algo_client_order_id=algo_client_id,
        )
        if protection is None:
            return
        position = self.allocator.position_store.get(protection.position_id)
        if position is None:
            raise LookupError(
                f"Protection owner {protection.position_id!r} no longer exists"
            )
        trigger_client_order_id = str(payload.get("clOrdId") or "")
        if position.status in {"open", "reducing"}:
            self.allocator.position_store.mark_pending_execution(
                protection.position_id,
                status="closing",
                exchange_order_id=order_id,
                client_order_id=trigger_client_order_id,
                metadata={
                    "order_action": "close",
                    "close_reason": "exchange protective stop triggered",
                    "execution_status": "protection_triggered",
                    "protection_algo_id": protection.algo_id,
                },
            )
        else:
            self.allocator.position_store.link_execution_order(
                protection.position_id,
                exchange_order_id=order_id,
                client_order_id=trigger_client_order_id,
                action="close",
                metadata={
                    "source": "protective_stop_trigger",
                    "algo_id": protection.algo_id,
                    "algo_client_order_id": protection.algo_client_order_id,
                },
            )
        self.allocator.position_store.update_protection(
            protection.position_id,
            status="triggered",
            trigger_order_id=order_id,
            metadata={"trigger_detected_at": datetime.now(timezone.utc).isoformat()},
        )
        status["protection_triggers_linked"] += 1
        self.allocator.audit_store.create(
            id=f"protection-trigger:{protection.algo_id}:{order_id}",
            type="position.protection_triggered",
            source=self.name,
            payload={
                "position_id": protection.position_id,
                "algo_id": protection.algo_id,
                "algo_client_order_id": protection.algo_client_order_id,
                "trigger_order_id": order_id,
            },
        )

    def _record_fill_rejection(
        self,
        raw_fill: dict[str, Any],
        error: str,
        *,
        category: str,
    ) -> None:
        bill_id = str(raw_fill.get("billId") or "")
        trade_id = str(raw_fill.get("tradeId") or raw_fill.get("fillId") or "")
        reference = bill_id or trade_id or "unknown"
        self.allocator.audit_store.create(
            id=f"fill-rejection:{reference}",
            type="execution.fill_rejected",
            source=self.name,
            payload={
                "category": category,
                "bill_id": bill_id,
                "fill_id": trade_id,
                "exchange_order_id": str(raw_fill.get("ordId") or ""),
                "instrument_id": str(raw_fill.get("instId") or ""),
                "error": error,
            },
        )
        self.publish_event(
            "execution.fill_rejected",
            {
                "category": category,
                "bill_id": bill_id,
                "fill_id": trade_id,
                "error": error,
            },
        )

    def _reconcile_pending_orders(self, status: dict[str, Any]) -> None:
        if self.client is None:
            return
        for position in self.allocator.position_store.list_pending_executions():
            order_id = position.exchange_order_id
            client_order_id = position.client_order_id
            try:
                orders = self.client.get_order(
                    position.inst_id,
                    order_id=order_id,
                    client_order_id=client_order_id,
                )
            except Exception as exc:
                status["order_errors"] += 1
                logger.warning("Failed to inspect OKX order %s: %s", order_id, exc)
                continue
            status["orders_checked"] += 1
            if not orders:
                if (
                    client_order_id
                    and not order_id
                    and self._position_age_seconds(position) >= self.stale_after_seconds
                ):
                    recovered = self.allocator.position_store.recover_client_order_intent(
                        position.id,
                        client_order_id=client_order_id,
                        execution_status="client_order_not_found",
                    )
                    if recovered is not None:
                        if recovered.status == "failed" and recovered.trade_id:
                            self.allocator.trade_store.mark_trade_failed(
                                recovered.trade_id,
                                reason="prepared client order not found on OKX",
                            )
                        status["missing_client_orders_recovered"] += 1
                        self.allocator.audit_store.create(
                            type="position.client_order_not_found",
                            source=self.name,
                            payload={
                                "strategy_id": recovered.strategy_id,
                                "position_id": recovered.id,
                                "trade_id": recovered.trade_id,
                                "client_order_id": client_order_id,
                                "recovered_status": recovered.status,
                            },
                        )
                        self._rearm_protection_if_needed(
                            recovered,
                            order_id="",
                            status=status,
                        )
                    continue
                status["order_errors"] += 1
                continue
            order = orders[0]
            recovered_order_id = str(order.get("ordId") or order_id or "")
            if not order_id and client_order_id and recovered_order_id:
                linked = self.allocator.position_store.link_exchange_order(
                    position.id,
                    client_order_id=client_order_id,
                    exchange_order_id=recovered_order_id,
                    metadata={"execution_status": "exchange_order_recovered"},
                )
                if linked is not None:
                    position = linked
                    order_id = recovered_order_id
                    status["client_orders_linked"] += 1
                    self.allocator.audit_store.create(
                        type="position.exchange_order_recovered",
                        source=self.name,
                        payload={
                            "strategy_id": position.strategy_id,
                            "position_id": position.id,
                            "trade_id": position.trade_id,
                            "client_order_id": client_order_id,
                            "exchange_order_id": order_id,
                        },
                    )
            if not order_id:
                status["order_errors"] += 1
                continue
            order_state = str(order.get("state") or "").lower()
            if order_state in self.TERMINAL_ORDER_STATES:
                self._recover_terminal_order(
                    position=position,
                    order_id=order_id,
                    order_state=order_state,
                    status=status,
                    source="rest",
                )
                continue
            if order_state == "filled":
                status["filled_awaiting_allocation"] += 1
                observed, count, alerted = (
                    self.allocator.position_store.record_filled_without_allocation(
                        position.id,
                        exchange_order_id=order_id,
                    )

                )
                if (
                    observed is not None
                    and count >= self.missing_fill_alert_after
                    and not alerted
                ):
                    try:
                        self.allocator.audit_store.create(
                            id=f"missing-fill:{observed.id}:{order_id}",
                            type="position.filled_without_allocation",
                            source=self.name,
                            payload={
                                "strategy_id": observed.strategy_id,
                                "position_id": observed.id,
                                "trade_id": observed.trade_id,
                                "exchange_order_id": order_id,
                                "observation_count": count,
                            },
                        )
                    except Exception as exc:
                        status["order_errors"] += 1
                        logger.warning(
                            "Failed to persist missing-fill alert for %s: %s",
                            order_id,
                            exc,
                        )
                        continue
                    if self.allocator.position_store.mark_filled_without_allocation_alerted(
                        observed.id,
                        exchange_order_id=order_id,
                    ):
                        status["missing_fill_alerts"] += 1
                        self.publish_event(
                            "execution.filled_without_allocation",
                            {
                                "position_id": observed.id,
                                "trade_id": observed.trade_id,
                                "exchange_order_id": order_id,
                                "observation_count": count,
                            },
                        )
                continue
            if (
                order_state in self.ACTIVE_ORDER_STATES
                and self._order_age_seconds(order) >= self.stale_after_seconds
            ):
                if not self.allow_order_mutations:
                    continue
                if self.allocator.position_store.is_order_cancel_requested(
                    position.id,
                    exchange_order_id=order_id,
                ):
                    continue
                try:
                    response = self.client.cancel_order(position.inst_id, order_id)
                except Exception as exc:
                    status["order_errors"] += 1
                    logger.warning("Failed to cancel stale OKX order %s: %s", order_id, exc)
                    continue
                cancel_code = str(response.get("sCode") or "") if response else ""
                if cancel_code not in {"", "0"}:
                    status["order_errors"] += 1
                    logger.warning(
                        "OKX rejected cancellation for %s (sCode=%s)",
                        order_id,
                        cancel_code,
                    )
                    continue
                if response and self.allocator.position_store.mark_order_cancel_requested(
                    position.id,
                    exchange_order_id=order_id,
                ):
                    status["stale_cancel_requested"] += 1
                    self.publish_event(
                        "execution.stale_order_cancel_requested",
                        {
                            "position_id": position.id,
                            "exchange_order_id": order_id,
                            "order_state": order_state,
                        },
                    )

    def _recover_terminal_order(
        self,
        *,
        position: Any,
        order_id: str,
        order_state: str,
        status: dict[str, Any],
        source: str,
    ) -> None:
        recovered = self.allocator.position_store.recover_terminal_order(
            position.id,
            exchange_order_id=order_id,
            order_state=order_state,
        )
        if recovered is None:
            return
        if recovered.status == "failed" and recovered.trade_id:
            self.allocator.trade_store.mark_trade_failed(
                recovered.trade_id,
                reason=f"entry order {order_state}",
            )
        status["terminal_recovered"] += 1
        payload = {
            "strategy_id": recovered.strategy_id,
            "position_id": recovered.id,
            "trade_id": recovered.trade_id,
            "exchange_order_id": order_id,
            "order_state": order_state,
            "recovered_status": recovered.status,
            "confirmation_source": source,
        }
        self.allocator.audit_store.create(
            id=f"terminal-order:{order_id}:{order_state}",
            type="position.order_terminal_recovered",
            source=self.name,
            payload=payload,
        )
        self.publish_event("execution.order_terminal_recovered", payload)
        self._rearm_protection_if_needed(
            recovered,
            order_id=order_id,
            status=status,
        )

    def _rearm_protection_if_needed(
        self,
        position: Any,
        *,
        order_id: str,
        status: dict[str, Any],
    ) -> None:
        if not self.allow_order_mutations:
            return
        protection = self.allocator.position_store.get_protection(position.id)
        if (
            position.status == "open"
            and protection is not None
            and protection.status in {"canceled", "triggered"}
        ):
            try:
                if self.protection_service is None:
                    raise PositionProtectionError(
                        "protective-stop lifecycle service is unavailable"
                    )
                self.protection_service.protect(position.id)
                resolved_at = datetime.now(timezone.utc)
                current = self.allocator.position_store.get(position.id)
                try:
                    gap_metadata = json.loads(
                        current.metadata_json if current is not None else "{}"
                    )
                    started = datetime.fromisoformat(
                        str(gap_metadata.get("protection_gap_started_at") or "").replace(
                            "Z", "+00:00"
                        )
                    )
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    gap_seconds = max(0.0, (resolved_at - started).total_seconds())
                except (json.JSONDecodeError, ValueError, AttributeError):
                    gap_seconds = 0.0
                self.allocator.position_store.merge_metadata(
                    position.id,
                    {
                        "protection_canceled_for_close": False,
                        "protection_gap_resolved_at": resolved_at.isoformat(),
                        "protection_gap_seconds": gap_seconds,
                    },
                )
                status["protection_rearmed"] += 1
            except PositionProtectionError as exc:
                status["protection_errors"] += 1
                AccountRiskStore(self.allocator.trade_store.db_path).set_entries_enabled(
                    False
                )
                disable_entry_order_placement()
                self.allocator.audit_store.create(
                    type="position.protection_rearm_failed",
                    source=self.name,
                    payload={
                        "position_id": position.id,
                        "exchange_order_id": order_id,
                        "error": str(exc),
                    },
                )

    def _reconcile_owned_protections(self, status: dict[str, Any]) -> None:
        if self.protection_service is None:
            return
        for position in self.allocator.position_store.list(status="open", limit=500):
            remaining = position.remaining_quantity or position.opened_quantity or 0.0
            if remaining <= 0:
                continue
            status["protections_checked"] += 1
            protection = self.allocator.position_store.get_protection(position.id)
            if protection is not None and protection.status == "triggered":
                status["protection_errors"] += 1
                self._disable_entries_for_execution_error()
                continue
            try:
                self.protection_service.verify_active(position.id)
                continue
            except PositionProtectionError:
                pass
            if not self.allow_order_mutations:
                continue
            try:
                self.protection_service.protect(position.id)
                status["protection_rearmed"] += 1
            except PositionProtectionError as exc:
                status["protection_errors"] += 1
                self._disable_entries_for_execution_error()
                self.allocator.audit_store.create(
                    type="position.protection_reconcile_failed",
                    source=self.name,
                    payload={"position_id": position.id, "error": str(exc)},
                )

    def _track_protection_gaps(self, status: dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        for position in self.allocator.position_store.list_active():
            remaining = position.remaining_quantity or position.opened_quantity or 0.0
            if remaining <= 0:
                continue
            protection = self.allocator.position_store.get_protection(position.id)
            if protection is not None and protection.status == "active":
                continue
            status["unprotected_positions"] += 1
            self._disable_entries_for_execution_error()
            try:
                metadata = json.loads(position.metadata_json or "{}")
            except json.JSONDecodeError:
                metadata = {}
            started_at = str(metadata.get("protection_gap_started_at") or "")
            try:
                started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
            except ValueError:
                started = now
                started_at = started.isoformat()
                self.allocator.position_store.merge_metadata(
                    position.id,
                    {"protection_gap_started_at": started_at},
                )
            age = max(0.0, (now - started).total_seconds())
            status["oldest_protection_gap_seconds"] = max(
                status["oldest_protection_gap_seconds"], age
            )
            if (
                age >= self.protection_gap_alert_seconds
                and not metadata.get("protection_gap_alerted_at")
            ):
                alerted_at = now.isoformat()
                self.allocator.position_store.merge_metadata(
                    position.id,
                    {"protection_gap_alerted_at": alerted_at},
                )
                self.allocator.audit_store.create(
                    type="position.protection_gap_exceeded",
                    source=self.name,
                    payload={
                        "position_id": position.id,
                        "status": position.status,
                        "remaining_quantity": remaining,
                        "gap_started_at": started_at,
                        "gap_seconds": age,
                    },
                )
                self.publish_event(
                    "position.protection_gap_exceeded",
                    {
                        "position_id": position.id,
                        "gap_seconds": age,
                    },
                )
                status["protection_gap_alerts"] += 1

    def _disable_entries_for_execution_error(self) -> None:
        AccountRiskStore(self.allocator.trade_store.db_path).set_entries_enabled(False)
        disable_entry_order_placement()

    @staticmethod
    def _order_age_seconds(order: dict[str, Any]) -> float:
        raw_timestamp = order.get("uTime") or order.get("cTime")
        try:
            timestamp = int(str(raw_timestamp)) / 1000
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, datetime.now(timezone.utc).timestamp() - timestamp)

    @staticmethod
    def _position_age_seconds(position: Any) -> float:
        try:
            timestamp = datetime.fromisoformat(position.updated_at).timestamp()
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, datetime.now(timezone.utc).timestamp() - timestamp)

    def teardown(self) -> None:
        if self.private_order_stream is not None:
            self.private_order_stream.stop()
        logger.info("ExecutionFillService shutting down.")
