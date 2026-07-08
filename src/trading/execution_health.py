"""Derive fail-closed, freshness-aware execution ingestion health."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal


ExecutionHealthState = Literal["healthy", "degraded", "stale", "unavailable"]


def evaluate_execution_health(
    status: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    stale_after_seconds: float = 15.0,
) -> dict[str, Any]:
    """Return current health without treating an old successful poll as current."""
    if not status or not status.get("service_available"):
        return {
            **(status or {}),
            "service_available": False,
            "healthy": False,
            "health_state": "unavailable",
            "health_reasons": ["execution fill service status is unavailable"],
            "status_age_seconds": None,
        }

    current = now or datetime.now(timezone.utc)
    updated_at = _parse_time(status.get("updated_at"))
    age = None if updated_at is None else max(0.0, (current - updated_at).total_seconds())
    # A producer may stamp its own realistic tolerance (e.g. ExecutionFillService
    # sizes this from its configured REST poll cadence) so a single legitimately
    # slow tick is not misreported as an outage. Callers that omit it keep the
    # generic default below.
    effective_stale_after_seconds = status.get("stale_after_seconds") or stale_after_seconds
    if age is None or age > effective_stale_after_seconds:
        return {
            **status,
            "healthy": False,
            "health_state": "stale",
            "health_reasons": ["execution fill service heartbeat is stale"],
            "status_age_seconds": age,
        }

    reasons: list[str] = []
    if int(status.get("cursor_errors") or 0) or status.get("cursor_error"):
        reasons.append("REST fill cursor has an error")
    if status.get("cursor_in_progress") or not status.get("caught_up"):
        reasons.append("REST fill catch-up is incomplete")
    if status.get("websocket_enabled") and not status.get("websocket_connected"):
        reasons.append("private order stream is disconnected")
    if int(status.get("websocket_drops_pending_catchup") or 0) > 0:
        reasons.append("dropped private-stream events await REST catch-up")
    for field, label in (
        ("conflicts", "fill allocation conflicts require review"),
        ("invalid", "invalid fills require review"),
        ("filled_awaiting_allocation", "confirmed fills await allocation"),
        ("missing_fill_alerts", "orders are missing confirmed fills"),
        ("order_errors", "order reconciliation has errors"),
        ("protection_errors", "position protection reconciliation has errors"),
        ("unprotected_positions", "positions lack confirmed protection"),
        ("rule_materialization_errors", "protection rule materialization has errors"),
    ):
        if int(status.get(field) or 0) > 0:
            reasons.append(label)

    return {
        **status,
        "healthy": not reasons,
        "health_state": "healthy" if not reasons else "degraded",
        "health_reasons": reasons,
        "status_age_seconds": age,
    }


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
