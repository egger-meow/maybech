"""Shared provider contract: timeout/retry/backoff and the observation interface."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Callable, TypeVar

import requests

from src.market_intelligence.models import MetricObservation

T = TypeVar("T")


class ProviderError(RuntimeError):
    """A provider fetch failed after exhausting its bounded retries."""

    def __init__(self, message: str, *, category: str = "unknown") -> None:
        super().__init__(message)
        self.category = category


def classify_exception(exc: BaseException) -> str:
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "connection"
    if isinstance(exc, requests.HTTPError):
        return "http_error"
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "parse_error"
    return "unknown"


def request_with_retry(
    fetch: Callable[[], T],
    *,
    retries: int = 2,
    backoff_seconds: float = 0.05,
) -> T:
    """Run ``fetch`` with bounded retries and linear backoff.

    Wraps the final failure in :class:`ProviderError` with a classified
    category so provider-status reporting can distinguish timeout/connection/
    HTTP failures from parse errors without every provider reimplementing it.
    """
    last_exc: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            return fetch()
        except Exception as exc:  # noqa: BLE001 - retried here, reclassified below
            last_exc = exc
            if attempt < retries:
                time.sleep(backoff_seconds * (attempt + 1))
    assert last_exc is not None  # loop always sets it before falling through
    raise ProviderError(str(last_exc), category=classify_exception(last_exc)) from last_exc


def coerce_float(raw: object) -> float | None:
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return value


class MarketDataProvider(ABC):
    """A single external market-data source, owning one or more metric_ids."""

    provider_id: str
    min_refresh_interval_seconds: float = 300.0
    metric_ids: tuple[str, ...] = ()

    def is_configured(self) -> bool:
        """Whether this instance can actually fetch (e.g. has a live client).

        A provider that always returns True here (the default) is expected to
        work from process start. Providers that depend on optional runtime
        wiring (an exchange client not available in every mode) override this
        so the service layer can skip them quietly instead of recording a
        permanent "failed" run every cycle.
        """
        return True

    @abstractmethod
    def fetch_observations(self) -> list[MetricObservation]:
        """Fetch current observations for this provider's metrics.

        Must raise on total failure rather than returning an empty/partial
        result silently — the service layer is what decides stale-while-
        revalidate fallback behavior, not the provider.
        """
