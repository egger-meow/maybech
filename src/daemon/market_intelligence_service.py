"""Daemon service that periodically syncs external market-intelligence providers."""

from __future__ import annotations

from src.daemon.service import DaemonService
from src.market_intelligence.service import MarketIntelligenceService
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class MarketIntelligenceSyncService(DaemonService):
    """Ingests external macro/sentiment/valuation metrics into persisted history.

    Ticks frequently (below), but each provider gates its own HTTP calls
    against its registered ``min_refresh_interval_seconds`` (see
    MarketIntelligenceService._due), so cadence stays per-provider rather than
    one global "updates every N seconds" promise.
    """

    name = "market_intelligence"
    interval = 60.0

    def __init__(self, *, service: MarketIntelligenceService | None = None) -> None:
        super().__init__()
        self._service = service

    def setup(self) -> None:
        if self._service is None:
            self._service = MarketIntelligenceService()
        logger.info("MarketIntelligenceSyncService setup complete")

    def tick(self) -> None:
        if self._service is None:
            raise RuntimeError("MarketIntelligenceSyncService is not set up")
        for run in self._service.sync_all():
            if run.status == "failed":
                self.publish_event(
                    "market_intelligence.provider_failed",
                    {
                        "provider_id": run.provider_id,
                        "error_category": run.error_category,
                        "error_detail": run.error_detail,
                    },
                )

    def teardown(self) -> None:
        logger.info("MarketIntelligenceSyncService shutting down.")
