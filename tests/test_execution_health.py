from datetime import datetime, timedelta, timezone

from src.trading.execution_health import evaluate_execution_health


def _status(now: datetime) -> dict:
    return {
        "service_available": True,
        "updated_at": now.isoformat(),
        "caught_up": True,
        "cursor_in_progress": False,
        "cursor_errors": 0,
        "cursor_error": "",
        "websocket_enabled": True,
        "websocket_connected": True,
    }


def test_execution_health_is_fail_closed_when_missing_or_stale():
    now = datetime.now(timezone.utc)

    missing = evaluate_execution_health(None, now=now)
    stale = evaluate_execution_health(
        _status(now - timedelta(seconds=16)),
        now=now,
    )

    assert missing["health_state"] == "unavailable"
    assert missing["healthy"] is False
    assert stale["health_state"] == "stale"
    assert stale["healthy"] is False


def test_execution_health_tracks_disconnect_catchup_errors_and_recovery():
    now = datetime.now(timezone.utc)
    disconnected = {**_status(now), "websocket_connected": False}
    cursor_error = {
        **_status(now),
        "caught_up": False,
        "cursor_in_progress": True,
        "cursor_errors": 1,
        "cursor_error": "OKX unavailable",
    }

    disconnected_health = evaluate_execution_health(disconnected, now=now)
    cursor_health = evaluate_execution_health(cursor_error, now=now)
    recovered = evaluate_execution_health(_status(now), now=now)

    assert disconnected_health["health_state"] == "degraded"
    assert "disconnected" in disconnected_health["health_reasons"][0]
    assert cursor_health["health_state"] == "degraded"
    assert len(cursor_health["health_reasons"]) == 2
    assert recovered["health_state"] == "healthy"
    assert recovered["health_reasons"] == []


def test_execution_health_discloses_correctness_failures():
    now = datetime.now(timezone.utc)
    status = {
        **_status(now),
        "conflicts": 1,
        "missing_fill_alerts": 1,
        "protection_errors": 1,
    }

    health = evaluate_execution_health(status, now=now)

    assert health["healthy"] is False
    assert health["health_state"] == "degraded"
    assert health["health_reasons"] == [
        "fill allocation conflicts require review",
        "orders are missing confirmed fills",
        "position protection reconciliation has errors",
    ]


def test_execution_health_keeps_dropped_stream_events_fail_closed_until_catchup():
    now = datetime.now(timezone.utc)

    dropped = evaluate_execution_health(
        {**_status(now), "websocket_drops_pending_catchup": 2},
        now=now,
    )
    reconciled = evaluate_execution_health(
        {**_status(now), "websocket_dropped_events": 2},
        now=now,
    )

    assert dropped["health_state"] == "degraded"
    assert "await REST catch-up" in dropped["health_reasons"][0]
    assert reconciled["health_state"] == "healthy"
