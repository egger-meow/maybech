"""Poll authenticated OKX fills and allocate matching logical positions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.daemon.service import DaemonService
from src.exchange.client import OKXClient
from src.exchange.fills import normalize_okx_fill
from src.trading.logical_position_store import AllocationConflictError
from src.trading.execution_cursor_store import ExecutionCursorStore
from src.trading.execution_allocation import ExecutionAllocationService
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


class ExecutionFillService(DaemonService):
    """Provide polling catch-up for fills missed before or between websocket sessions."""

    name = "execution_fills"
    interval = 5.0
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

    def setup(self) -> None:
        if self.client is None:
            self.client = OKXClient()
        logger.info("ExecutionFillService setup complete.")

    def tick(self) -> None:
        if self.client is None:
            raise RuntimeError("ExecutionFillService is not set up")
        status: dict[str, Any] = {
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
            "pages_fetched": 0,
            "caught_up": False,
            "cursor_in_progress": False,
            "history_exhausted": False,
            "high_water_bill_id": "",
            "next_after_bill_id": "",
            "cursor_errors": 0,
            "cursor_error": "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        catchup_error: Exception | None = None
        try:
            self._catch_up_fills(status)
        except Exception as exc:
            catchup_error = exc
            status["cursor_errors"] += 1
            status["cursor_error"] = str(exc)
            logger.error("OKX fill catch-up failed: %s", exc)

        self._reconcile_pending_orders(status)
        cursor = self.cursor_store.get(self.FILL_STREAM_ID)
        status["cursor_in_progress"] = cursor.in_progress
        status["high_water_bill_id"] = cursor.high_water_id
        status["next_after_bill_id"] = cursor.next_after_id

        if self.runtime is not None:
            self.runtime.set_value("execution.fills.status", status)
        self.publish_event("execution.fills_polled", status)
        if catchup_error is not None:
            raise catchup_error

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
            try:
                orders = self.client.get_order(position.inst_id, order_id)
            except Exception as exc:
                status["order_errors"] += 1
                logger.warning("Failed to inspect OKX order %s: %s", order_id, exc)
                continue
            status["orders_checked"] += 1
            if not orders:
                status["order_errors"] += 1
                continue
            order = orders[0]
            order_state = str(order.get("state") or "").lower()
            if order_state in self.TERMINAL_ORDER_STATES:
                recovered = self.allocator.position_store.recover_terminal_order(
                    position.id,
                    exchange_order_id=order_id,
                    order_state=order_state,
                )
                if recovered is None:
                    continue
                if recovered.status == "failed" and recovered.trade_id:
                    self.allocator.trade_store.mark_trade_failed(
                        recovered.trade_id,
                        reason=f"entry order {order_state}",
                    )
                status["terminal_recovered"] += 1
                self.allocator.audit_store.create(
                    type="position.order_terminal_recovered",
                    source=self.name,
                    payload={
                        "strategy_id": recovered.strategy_id,
                        "position_id": recovered.id,
                        "trade_id": recovered.trade_id,
                        "exchange_order_id": order_id,
                        "order_state": order_state,
                        "recovered_status": recovered.status,
                    },
                )
                self.publish_event(
                    "execution.order_terminal_recovered",
                    {
                        "position_id": recovered.id,
                        "exchange_order_id": order_id,
                        "order_state": order_state,
                        "recovered_status": recovered.status,
                    },
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

    @staticmethod
    def _order_age_seconds(order: dict[str, Any]) -> float:
        raw_timestamp = order.get("uTime") or order.get("cTime")
        try:
            timestamp = int(str(raw_timestamp)) / 1000
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, datetime.now(timezone.utc).timestamp() - timestamp)

    def teardown(self) -> None:
        logger.info("ExecutionFillService shutting down.")
