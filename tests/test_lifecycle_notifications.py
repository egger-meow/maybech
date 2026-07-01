from unittest.mock import MagicMock, patch

from src.daemon.events import RuntimeState
from src.daemon.lifecycle_notification_service import (
    LifecycleNotificationService,
    classify_lifecycle_event,
)
from src.notifications.email_alert import EmailNotifier
from src.trading.audit_event_store import AuditEventStore


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, ...]] = []

    def send(self, *parts: str) -> bool:
        self.messages.append(parts)
        return True


def test_lifecycle_service_delivers_only_new_supported_audits(tmp_path):
    store = AuditEventStore(str(tmp_path / "trades.db"))
    store.create(
        type="strategy.created",
        source="api",
        payload={"strategy_id": "old"},
    )
    line = RecordingNotifier()
    email = RecordingNotifier()
    service = LifecycleNotificationService(
        audit_store=store,
        line=line,  # type: ignore[arg-type]
        email=email,  # type: ignore[arg-type]
    )
    service.runtime = RuntimeState()
    service.setup()
    store.create(
        type="market.btc_regime",
        source="btc_regime",
        payload={"direction": "bullish"},
    )
    store.create(
        type="strategy.enabled",
        source="api",
        payload={"strategy_id": "strategy-a"},
    )

    service.tick()
    service.tick()

    assert len(line.messages) == 1
    assert "策略已啟用" in line.messages[0][0]
    assert "strategy-a" in line.messages[0][0]
    assert len(email.messages) == 1
    assert email.messages[0][0] == "Maybech｜策略已啟用"


def test_runtime_service_error_routes_as_safety_failure(tmp_path):
    line = RecordingNotifier()
    email = RecordingNotifier()
    runtime = RuntimeState()
    service = LifecycleNotificationService(
        audit_store=AuditEventStore(str(tmp_path / "trades.db")),
        line=line,  # type: ignore[arg-type]
        email=email,  # type: ignore[arg-type]
    )
    service.runtime = runtime
    service.setup()

    runtime.events.publish(
        "service.error",
        "account",
        {"stage": "tick", "error": "OKX timeout"},
    )
    service.tick()

    assert len(line.messages) == 1
    assert "執行環境／安全失敗" in line.messages[0][0]
    assert "OKX timeout" in line.messages[0][0]
    assert len(store_events := service.audit_store.list(
        event_type="runtime.safety_failure"
    )) == 1
    assert store_events[0].source == "account"


def test_category_mapping_covers_confirmed_position_lifecycle():
    assert classify_lifecycle_event(
        "position.allocation_confirmed", {"action": "open"}
    ) == "部位已開立"
    assert classify_lifecycle_event(
        "position.allocation_confirmed", {"action": "reduce"}
    ) == "部位已部分減倉"
    assert classify_lifecycle_event(
        "position.allocation_confirmed", {"action": "close"}
    ) == "部位已平倉"
    assert classify_lifecycle_event("position.close_condition_evaluated", {}) is None


def test_email_notifier_sends_once_with_equivalent_message_cooldown():
    with patch("src.notifications.email_alert.settings") as mocked_settings:
        mocked_settings.NOTIFICATION_COOLDOWN_SECONDS = 300
        mocked_settings.EMAIL_SENDER = "sender@example.com"
        mocked_settings.EMAIL_PASSWORD = "app-password"
        mocked_settings.EMAIL_RECEIVER = "receiver@example.com"
        mocked_settings.EMAIL_SMTP_HOST = "smtp.example.com"
        mocked_settings.EMAIL_SMTP_PORT = 587
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        with patch("src.notifications.email_alert.smtplib.SMTP", return_value=smtp):
            notifier = EmailNotifier()
            first = notifier.send("Maybech｜策略遭封鎖", "原因：風險上限")
            duplicate = notifier.send(
                "  Maybech｜策略遭封鎖 ",
                " 原因：風險上限 ",
            )

    assert first is True
    assert duplicate is False
    smtp.sendmail.assert_called_once()
