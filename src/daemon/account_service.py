"""Daemon service that publishes account and position snapshots."""

from __future__ import annotations

from datetime import datetime, timezone

from src.daemon.service import DaemonService
from src.exchange.client import OKXClient
from src.monitor.dashboard import Dashboard
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.instrument_metadata import InstrumentMetadataStore
from src.trading.position_import import PositionRecoveryService
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


class AccountSnapshotService(DaemonService):
    """Read-only account, position, and order monitor for UI/API clients."""

    name = "account"
    interval = 15.0

    def __init__(self, *, position_store: LogicalPositionStore | None = None) -> None:
        super().__init__()
        self.client = None
        self.dashboard = None
        self.position_store = position_store or LogicalPositionStore()
        self.instrument_store = InstrumentMetadataStore(self.position_store.db_path)
        self.recovery = PositionRecoveryService(self.position_store)

    def setup(self) -> None:
        self.client = OKXClient()
        self.dashboard = Dashboard(self.client)
        self.instrument_store.refresh_if_stale(self.client)
        logger.info("AccountSnapshotService setup complete.")

    def tick(self) -> None:
        if self.dashboard is None:
            raise RuntimeError("AccountSnapshotService is not set up")

        self.instrument_store.refresh_if_stale(self.client)
        snapshot = {
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "summary": self.dashboard.get_account_summary(),
            "positions": self.dashboard.get_open_positions(),
            "orders": self.dashboard.get_recent_trades(limit=20),
        }
        recovered = self.recovery.reconcile(snapshot["positions"])

        if self.runtime is not None:
            self.runtime.set_value("account.snapshot", snapshot)
        self.publish_event(
            "account.snapshot",
            {
                "position_count": len(snapshot["positions"]),
                "order_count": len(snapshot["orders"]),
                "summary": snapshot["summary"],
                "recovered_position_ids": [position.id for position in recovered],
            },
        )

    def teardown(self) -> None:
        logger.info("AccountSnapshotService shutting down.")
