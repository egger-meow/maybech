"""Orchestrates providers -> storage -> freshness for the market intelligence layer."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.market_intelligence import registry
from src.market_intelligence.freshness import compute_freshness
from src.market_intelligence.models import ProviderSyncRun
from src.market_intelligence.providers.alternative_me import AlternativeMeProvider
from src.market_intelligence.providers.base import MarketDataProvider
from src.market_intelligence.providers.bitcoin_data import BitcoinDataMvrvProvider
from src.market_intelligence.providers.coingecko import CoinGeckoGlobalProvider
from src.market_intelligence.providers.defillama import DefiLlamaStablecoinProvider
from src.market_intelligence.providers.okx import OKXMarketProvider
from src.market_intelligence.storage.metric_store import MetricStore
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


def default_providers(*, exchange_client: Any | None = None) -> list[MarketDataProvider]:
    """The full canonical provider set.

    ``OKXMarketProvider`` is always included, with or without a live client,
    so provider-status reporting is complete regardless of which process
    constructed this service; an unconfigured client just means
    ``is_configured()`` is False and the service skips actually syncing it
    (see MarketIntelligenceService.sync_provider).
    """
    return [
        AlternativeMeProvider(),
        BitcoinDataMvrvProvider(),
        CoinGeckoGlobalProvider(),
        DefiLlamaStablecoinProvider(),
        OKXMarketProvider(exchange_client),
    ]


class MarketIntelligenceService:
    def __init__(
        self,
        *,
        store: MetricStore | None = None,
        providers: list[MarketDataProvider] | None = None,
    ) -> None:
        self.store = store or MetricStore()
        self.providers = providers if providers is not None else default_providers()

    def _due(self, provider: MarketDataProvider, *, now: datetime) -> bool:
        last_run = self.store.latest_sync_run(provider.provider_id)
        if last_run is None:
            return True
        try:
            started = datetime.fromisoformat(last_run.started_at)
        except ValueError:
            return True
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        return (now - started).total_seconds() >= provider.min_refresh_interval_seconds

    def sync_provider(self, provider: MarketDataProvider, *, force: bool = False) -> ProviderSyncRun:
        """Fetch+persist one provider's observations; never raises.

        A provider failure is recorded as a `failed` sync run and returned,
        isolating it from every other provider and from the caller — this is
        what keeps one broken external source from breaking API responses.
        """
        now = datetime.now(timezone.utc)
        started_at = now.isoformat()
        if not provider.is_configured():
            run = ProviderSyncRun(
                provider_id=provider.provider_id,
                started_at=started_at,
                completed_at=started_at,
                status="skipped",
                records_fetched=0,
                records_stored=0,
                error_category="not_configured",
            )
            self.store.record_sync_run(run)
            return run
        if not force and not self._due(provider, now=now):
            run = ProviderSyncRun(
                provider_id=provider.provider_id,
                started_at=started_at,
                completed_at=started_at,
                status="skipped",
                records_fetched=0,
                records_stored=0,
            )
            self.store.record_sync_run(run)
            return run
        try:
            observations = provider.fetch_observations()
        except Exception as exc:  # noqa: BLE001 - provider isolation boundary
            category = getattr(exc, "category", None) or (
                "parse_error" if isinstance(exc, (ValueError, KeyError, TypeError)) else "unknown"
            )
            logger.warning("market intelligence provider %s failed: %s", provider.provider_id, exc)
            run = ProviderSyncRun(
                provider_id=provider.provider_id,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                status="failed",
                records_fetched=0,
                records_stored=0,
                error_category=str(category),
                error_detail=str(exc)[:500],
            )
            self.store.record_sync_run(run)
            return run

        stored = self.store.insert_observations(observations)
        run = ProviderSyncRun(
            provider_id=provider.provider_id,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            status="success",
            records_fetched=len(observations),
            records_stored=stored,
        )
        self.store.record_sync_run(run)
        return run

    def sync_all(self, *, force: bool = False) -> list[ProviderSyncRun]:
        return [self.sync_provider(provider, force=force) for provider in self.providers]

    def get_metric(self, metric_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        definition = registry.get_definition(metric_id)
        if definition is None:
            return {"metric_id": metric_id, "unavailable_reason": "unknown metric_id"}
        observation = self.store.latest(metric_id)
        current = now or datetime.now(timezone.utc)
        freshness = compute_freshness(
            observation.observed_at if observation else None,
            definition.freshness_ttl_seconds,
            now=current,
        )
        return {
            "metric_id": definition.metric_id,
            "name": definition.name,
            "pillar": definition.pillar,
            "unit": definition.unit,
            "scope": definition.scope,
            "source_kind": definition.source_kind,
            "value": observation.value if observation else None,
            "observed_at": observation.observed_at if observation else None,
            "source_provider": observation.source_provider if observation else None,
            "freshness": freshness,
            "is_estimated": observation.is_estimated if observation else False,
            "methodology_version": definition.methodology_version,
            "caveats": definition.caveats,
            "unavailable_reason": None if observation else "no observation ingested yet",
        }

    def get_all_metrics(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        return [self.get_metric(definition.metric_id, now=now) for definition in registry.all_definitions()]

    def get_series(
        self,
        metric_id: str,
        *,
        start: str | None = None,
        end: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        definition = registry.get_definition(metric_id)
        if definition is None:
            return {"metric_id": metric_id, "unit": "", "points": [], "unavailable_reason": "unknown metric_id"}
        observations = self.store.history(metric_id, start=start, end=end, limit=limit)
        return {
            "metric_id": metric_id,
            "unit": definition.unit,
            "points": [{"observed_at": obs.observed_at, "value": obs.value} for obs in observations],
            "unavailable_reason": None if observations else "no observations stored for this range yet",
        }

    def get_provider_status(self) -> list[dict[str, Any]]:
        """Report each registered provider's health from persisted sync history.

        ``enabled`` deliberately reads the most recent *persisted* run rather
        than calling ``provider.is_configured()`` on this instance: API
        requests construct a throwaway ``MarketIntelligenceService()`` with no
        exchange client, which would always report OKX-scoped providers as
        disabled regardless of whether the long-running daemon instance is
        actually configured and syncing them. The shared SQLite history is
        the one source of truth both instances agree on.
        """
        statuses: list[dict[str, Any]] = []
        for provider in self.providers:
            recent = self.store.recent_sync_runs(provider.provider_id, limit=20)
            last_success = next((run for run in recent if run.status == "success"), None)
            last_attempt = next((run for run in recent if run.status != "skipped"), None)
            not_configured = bool(recent) and recent[0].error_category == "not_configured"
            statuses.append(
                {
                    "provider_id": provider.provider_id,
                    "metrics": list(provider.metric_ids),
                    "enabled": not not_configured,
                    "last_attempt_at": last_attempt.started_at if last_attempt else None,
                    "last_success_at": last_success.completed_at if last_success else None,
                    "last_error_category": (
                        last_attempt.error_category
                        if last_attempt is not None and last_attempt.status == "failed"
                        else None
                    ),
                    "consecutive_failures": self.store.consecutive_failures(provider.provider_id),
                }
            )
        return statuses
