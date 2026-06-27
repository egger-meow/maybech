"""Poll authenticated OKX fills and allocate matching logical positions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.daemon.service import DaemonService
from src.exchange.client import OKXClient
from src.exchange.fills import normalize_okx_fill
from src.trading.logical_position_store import AllocationConflictError
from src.trading.execution_allocation import ExecutionAllocationService
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


class ExecutionFillService(DaemonService):
    """Provide polling catch-up for fills missed before or between websocket sessions."""

    name = "execution_fills"
    interval = 5.0

    def __init__(
        self,
        *,
        client: OKXClient | None = None,
        allocator: ExecutionAllocationService | None = None,
    ) -> None:
        super().__init__()
        self.client = client
        self.allocator = allocator or ExecutionAllocationService()

    def setup(self) -> None:
        if self.client is None:
            self.client = OKXClient()
        logger.info("ExecutionFillService setup complete.")

    def tick(self) -> None:
        if self.client is None:
            raise RuntimeError("ExecutionFillService is not set up")
        raw_fills = self.client.get_fills(inst_type="SWAP", limit="100")
        status: dict[str, Any] = {
            "fetched": len(raw_fills),
            "applied": 0,
            "idempotent": 0,
            "unmatched": 0,
            "invalid": 0,
            "conflicts": 0,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        for raw_fill in raw_fills:
            try:
                fill = normalize_okx_fill(raw_fill)
            except ValueError as exc:
                status["invalid"] += 1
                logger.warning("Ignoring invalid OKX fill: %s", exc)
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
                continue
            except ValueError as exc:
                status["invalid"] += 1
                logger.warning("Rejected OKX fill %s: %s", fill.fill_id, exc)
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

        if self.runtime is not None:
            self.runtime.set_value("execution.fills.status", status)
        self.publish_event("execution.fills_polled", status)

    def teardown(self) -> None:
        logger.info("ExecutionFillService shutting down.")
