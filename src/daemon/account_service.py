"""Daemon service that publishes account and position snapshots."""

from __future__ import annotations

from src.daemon.service import DaemonService
from src.exchange.client import OKXClient
from src.monitor.dashboard import Dashboard
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


class AccountSnapshotService(DaemonService):
    """Read-only account, position, and order monitor for UI/API clients."""

    name = "account"
    interval = 15.0

    def __init__(self) -> None:
        super().__init__()
        self.client = None
        self.dashboard = None

    def setup(self) -> None:
        self.client = OKXClient()
        self.dashboard = Dashboard(self.client)
        logger.info("AccountSnapshotService setup complete.")

    def tick(self) -> None:
        if self.dashboard is None:
            raise RuntimeError("AccountSnapshotService is not set up")

        snapshot = {
            "summary": self.dashboard.get_account_summary(),
            "positions": self.dashboard.get_open_positions(),
            "orders": self.dashboard.get_recent_trades(limit=20),
        }

        if self.runtime is not None:
            self.runtime.set_value("account.snapshot", snapshot)
        self.publish_event(
            "account.snapshot",
            {
                "position_count": len(snapshot["positions"]),
                "order_count": len(snapshot["orders"]),
                "summary": snapshot["summary"],
            },
        )

    def teardown(self) -> None:
        logger.info("AccountSnapshotService shutting down.")
