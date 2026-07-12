from src.daemon.market_intelligence_service import MarketIntelligenceSyncService
from src.market_intelligence.models import ProviderSyncRun
from src.market_intelligence.service import MarketIntelligenceService


class _StubService(MarketIntelligenceService):
    def __init__(self, runs):
        self._runs = runs

    def sync_all(self, *, force=False):
        return self._runs


def test_tick_publishes_event_on_provider_failure():
    published = []

    stub = _StubService(
        [
            ProviderSyncRun(
                provider_id="alternative_me",
                started_at="2026-07-10T00:00:00+00:00",
                completed_at="2026-07-10T00:00:01+00:00",
                status="failed",
                records_fetched=0,
                records_stored=0,
                error_category="timeout",
                error_detail="boom",
            ),
            ProviderSyncRun(
                provider_id="coingecko_global",
                started_at="2026-07-10T00:00:00+00:00",
                completed_at="2026-07-10T00:00:01+00:00",
                status="success",
                records_fetched=4,
                records_stored=4,
            ),
        ]
    )
    service = MarketIntelligenceSyncService(service=stub)
    service.publish_event = lambda event_type, payload=None: published.append((event_type, payload))

    service.tick()

    assert len(published) == 1
    assert published[0][0] == "market_intelligence.provider_failed"
    assert published[0][1]["provider_id"] == "alternative_me"


def test_tick_publishes_nothing_when_all_providers_succeed():
    published = []
    stub = _StubService(
        [
            ProviderSyncRun(
                provider_id="alternative_me",
                started_at="2026-07-10T00:00:00+00:00",
                completed_at="2026-07-10T00:00:01+00:00",
                status="success",
                records_fetched=1,
                records_stored=1,
            )
        ]
    )
    service = MarketIntelligenceSyncService(service=stub)
    service.publish_event = lambda event_type, payload=None: published.append((event_type, payload))

    service.tick()

    assert published == []


def test_tick_raises_if_setup_never_ran():
    service = MarketIntelligenceSyncService()
    try:
        service.tick()
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass
