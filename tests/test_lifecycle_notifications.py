from unittest.mock import MagicMock, patch

from src.daemon.events import RuntimeState
from src.daemon.lifecycle_notification_service import (
    LifecycleNotificationService,
    classify_lifecycle_event,
    format_lifecycle_message,
)
from src.notifications.email_alert import EmailNotifier
from src.trading.audit_event_store import AuditEventStore


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[tuple[str, ...]] = []

    def send(self, *parts: str) -> bool:
        self.messages.append(parts)
        return True


class FailingNotifier(RecordingNotifier):
    def send(self, *parts: str) -> bool:
        self.messages.append(parts)
        return False


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
        retry_base_seconds=0,
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
        retry_base_seconds=0,
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


def test_lifecycle_cursor_resumes_events_written_during_restart(tmp_path):
    store = AuditEventStore(str(tmp_path / "trades.db"))
    first = LifecycleNotificationService(
        audit_store=store,
        line=RecordingNotifier(),  # type: ignore[arg-type]
        email=RecordingNotifier(),  # type: ignore[arg-type]
    )
    first.setup()
    store.create(type="strategy.enabled", source="api", payload={"strategy_id": "a"})
    first.tick()
    first.teardown()
    store.create(type="strategy.disabled", source="api", payload={"strategy_id": "a"})

    line = RecordingNotifier()
    restarted = LifecycleNotificationService(
        audit_store=AuditEventStore(store.db_path),
        line=line,  # type: ignore[arg-type]
        email=RecordingNotifier(),  # type: ignore[arg-type]
    )
    restarted.setup()
    restarted.tick()

    assert len(line.messages) == 1
    assert "策略已停用" in line.messages[0][0]


def test_quarantined_fill_notifies_once_across_retries_and_restart(tmp_path):
    store = AuditEventStore(str(tmp_path / "trades.db"))
    raw_fill = {"billId": "bill-a", "tradeId": "fill-a"}
    line = RecordingNotifier()
    service = LifecycleNotificationService(
        audit_store=store,
        line=line,  # type: ignore[arg-type]
        email=RecordingNotifier(),  # type: ignore[arg-type]
    )
    service.setup()
    store.quarantine_fill_rejection(
        raw_fill=raw_fill,
        error="take_profit price must be above entry for long",
        category="rejected",
        source="execution_fills",
    )
    service.tick()
    store.quarantine_fill_rejection(
        raw_fill=raw_fill,
        error="take_profit price must be above entry for long",
        category="rejected",
        source="execution_fills",
    )
    service.tick()
    service.teardown()

    restarted_line = RecordingNotifier()
    restarted = LifecycleNotificationService(
        audit_store=AuditEventStore(store.db_path),
        line=restarted_line,  # type: ignore[arg-type]
        email=RecordingNotifier(),  # type: ignore[arg-type]
    )
    restarted.setup()
    restarted.tick()
    restarted.audit_store.quarantine_fill_rejection(
        raw_fill=raw_fill,
        error="stop_loss price must be below entry for long",
        category="rejected",
        source="execution_fills",
    )
    restarted.tick()

    assert len(line.messages) == 1
    assert len(restarted_line.messages) == 1
    assert "stop_loss price must be below entry" in restarted_line.messages[0][0]


def test_lifecycle_cursor_retries_failure_before_later_events(tmp_path):
    store = AuditEventStore(str(tmp_path / "trades.db"))
    line = FailingNotifier()
    service = LifecycleNotificationService(
        audit_store=store,
        line=line,  # type: ignore[arg-type]
        email=RecordingNotifier(),  # type: ignore[arg-type]
        retry_base_seconds=0,
    )
    service.setup()
    store.create(type="strategy.enabled", source="api", payload={"strategy_id": "a"})
    store.create(type="strategy.disabled", source="api", payload={"strategy_id": "b"})

    service.tick()
    assert len(line.messages) == 1

    replacement = RecordingNotifier()
    service.line = replacement  # type: ignore[assignment]
    service.tick()

    assert len(replacement.messages) == 2
    assert "策略已啟用" in replacement.messages[0][0]
    assert "策略已停用" in replacement.messages[1][0]


def test_lifecycle_retry_does_not_redeliver_acknowledged_channel(tmp_path):
    store = AuditEventStore(str(tmp_path / "trades.db"))
    line = RecordingNotifier()
    email = FailingNotifier()
    service = LifecycleNotificationService(
        audit_store=store,
        line=line,  # type: ignore[arg-type]
        email=email,  # type: ignore[arg-type]
        retry_base_seconds=0,
    )
    service.setup()
    store.create(type="strategy.enabled", source="api", payload={"strategy_id": "a"})

    service.tick()
    assert store.notification_acknowledgement_count("lifecycle_notifications") == 1
    service.email = RecordingNotifier()  # type: ignore[assignment]
    service.tick()

    assert len(line.messages) == 1
    assert len(service.email.messages) == 1  # type: ignore[attr-defined]
    assert store.notification_acknowledgement_count("lifecycle_notifications") == 0


def test_advancing_cursor_compacts_historical_channel_acknowledgements(tmp_path):
    store = AuditEventStore(str(tmp_path / "trades.db"))
    service = LifecycleNotificationService(
        audit_store=store,
        line=RecordingNotifier(),  # type: ignore[arg-type]
        email=RecordingNotifier(),  # type: ignore[arg-type]
    )
    service.setup()
    for index in range(25):
        store.create(
            type="strategy.enabled",
            source="api",
            payload={"strategy_id": f"strategy-{index}"},
        )

    service.tick()

    assert store.pending_delivery_count("lifecycle_notifications") == 0
    assert store.notification_acknowledgement_count("lifecycle_notifications") == 0


def test_lifecycle_failure_persists_health_and_bounded_backoff(tmp_path):
    store = AuditEventStore(str(tmp_path / "trades.db"))
    line = FailingNotifier()
    service = LifecycleNotificationService(
        audit_store=store,
        line=line,  # type: ignore[arg-type]
        email=RecordingNotifier(),  # type: ignore[arg-type]
        retry_base_seconds=30,
        retry_max_seconds=60,
    )
    service.setup()
    store.create(type="strategy.enabled", source="api", payload={"strategy_id": "a"})

    service.tick()
    service.tick()

    health = store.notification_delivery_health("lifecycle_notifications")["line"]
    assert len(line.messages) == 1
    assert health["consecutive_failures"] == 1
    assert health["last_failure_at"]
    assert health["next_retry_at"] > health["last_failure_at"]


def test_category_mapping_covers_confirmed_position_lifecycle():
    assert classify_lifecycle_event(
        "position.allocation_confirmed",
        {"action": "open", "strategy_id": "strategy-a"},
    ) == "策略進場已成交／部位已開立"
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


def test_category_mapping_covers_protection_trigger_and_kill_switch():
    assert (
        classify_lifecycle_event("position.protection_triggered", {})
        == "保護單已觸發（停損／停利）"
    )
    assert (
        classify_lifecycle_event("entry_control.killed", {})
        == "已停止進場（Kill Switch）"
    )
    assert (
        classify_lifecycle_event("entry_control.enabled", {})
        == "已恢復進場"
    )


def test_format_lifecycle_message_surfaces_pnl_price_reason_and_duration():
    payload = {
        "strategy_id": "strategy-a",
        "position_id": "pos-1",
        "inst_id": "BTC-USDT-SWAP",
        "side": "long",
        "quantity": 0.5,
        "entry_price": 60000.0,
        "exit_price": 61000.0,
        "realized_pnl": 500.0,
        "realized_pnl_pct": 1.67,
        "exit_reason": "take_profit",
        "entry_time": "2026-07-10T00:00:00+00:00",
        "exit_time": "2026-07-10T02:30:00+00:00",
    }
    message = format_lifecycle_message("部位已平倉", "position.allocation_confirmed", payload)

    assert "損益：+500.0000 USDT（+1.67%）　🟢 獲利" in message
    assert "進場價：60000.0" in message
    assert "出場價：61000.0" in message
    assert "原因：take_profit" in message
    assert "持倉時間：2h 30m" in message


def test_format_lifecycle_message_marks_losing_trade():
    payload = {"pnl": -120.5, "pnl_pct": -3.4}
    message = format_lifecycle_message("部位已平倉", "position.closed", payload)

    assert "🔴 虧損" in message
    assert "🟢" not in message


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


def test_email_notifier_escalates_gap_for_a_persistently_repeating_message():
    with patch("src.notifications.email_alert.settings") as mocked_settings:
        mocked_settings.NOTIFICATION_COOLDOWN_SECONDS = 100
        mocked_settings.EMAIL_SENDER = "sender@example.com"
        mocked_settings.EMAIL_PASSWORD = "app-password"
        mocked_settings.EMAIL_RECEIVER = "receiver@example.com"
        mocked_settings.EMAIL_SMTP_HOST = "smtp.example.com"
        mocked_settings.EMAIL_SMTP_PORT = 587
        smtp = MagicMock()
        smtp.__enter__.return_value = smtp
        clock = {"t": 0.0}
        with patch("src.notifications.email_alert.smtplib.SMTP", return_value=smtp), patch(
            "src.notifications.repeat_cooldown.time.monotonic",
            side_effect=lambda: clock["t"],
        ):
            notifier = EmailNotifier()
            for clock["t"] in (0, 100, 200):
                assert notifier.send("Maybech｜執行環境／安全失敗", "錯誤：OKX timeout") is True

            # A 4th identical alert right at the plain 100s cooldown is still
            # suppressed; the gap has escalated to 200s (100 * 2) by now.
            clock["t"] = 300
            assert notifier.send("Maybech｜執行環境／安全失敗", "錯誤：OKX timeout") is False
            clock["t"] = 400
            assert notifier.send("Maybech｜執行環境／安全失敗", "錯誤：OKX timeout") is True

    assert smtp.sendmail.call_count == 4
