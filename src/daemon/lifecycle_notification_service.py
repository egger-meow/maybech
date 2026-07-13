"""Deliver a bounded set of operator lifecycle events to configured channels."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from src.daemon.events import RuntimeEvent
from src.daemon.service import DaemonService
from src.notifications.email_alert import EmailNotifier
from src.notifications.line_bot import LineBotNotifier
from src.trading.audit_event_store import AuditEventRecord, AuditEventStore


_EXACT_CATEGORIES = {
    "strategy.created": "策略已建立",
    "strategy.enabled": "策略已啟用",
    "strategy.disabled": "策略已停用",
    "strategy.execution_blocked": "策略遭封鎖",
    "strategy.execution_delay_blocked": "策略延遲執行遭封鎖",
    "strategy.execution_failed": "策略執行失敗",
    "position.manual_open_simulated": "模擬部位已建立",
    "position.recovered_from_exchange": "發現外部部位",
    "position.reconciliation_manual_review": "部位需要人工檢查",
    "position.protection_rearm_failed": "部位保護重建失敗",
    "position.protection_reconcile_failed": "部位保護對帳失敗",
    "position.protection_triggered": "保護單已觸發（停損／停利）",
    "position.filled_without_allocation": "成交尚未分配",
    "execution.fill_rejected": "成交資料遭拒絕",
    "entry_control.killed": "已停止進場（Kill Switch）",
    "entry_control.enabled": "已恢復進場",
}


def classify_lifecycle_event(
    event_type: str,
    payload: dict[str, Any],
) -> str | None:
    """Return the supported operator category, excluding noisy market events."""
    if event_type == "strategy.execution_result":
        status = str(payload.get("execution_status") or payload.get("result") or "")
        if status in {"submitted", "simulated"}:
            return "策略進場已送出" if status == "submitted" else "策略模擬進場已執行"
        return None
    if event_type == "position.allocation_confirmed":
        action = str(payload.get("action") or "")
        if action == "open" and payload.get("strategy_id"):
            return "策略進場已成交／部位已開立"
        return {
            "open": "部位已開立",
            "reduce": "部位已部分減倉",
            "close": "部位已平倉",
        }.get(action)
    if event_type in {"position.reduced", "position.closed"}:
        return "部位已部分減倉" if event_type.endswith("reduced") else "部位已平倉"
    if event_type.endswith("_submission_failed") or event_type.endswith("_failed"):
        if event_type.startswith(("position.", "execution.", "strategy.")):
            return "執行失敗"
    return _EXACT_CATEGORIES.get(event_type)


def _format_pnl_line(payload: dict[str, Any]) -> str | None:
    """Render a prominent P&L summary line, preferring confirmed over dry-run fields."""
    for pnl_key, pct_key in (("realized_pnl", "realized_pnl_pct"), ("pnl", "pnl_pct")):
        raw = payload.get(pnl_key)
        if raw in (None, ""):
            continue
        try:
            pnl = float(raw)
        except (TypeError, ValueError):
            continue
        pct_text = ""
        raw_pct = payload.get(pct_key)
        if raw_pct not in (None, ""):
            try:
                pct_text = f"（{float(raw_pct):+.2f}%）"
            except (TypeError, ValueError):
                pass
        indicator = "🟢 獲利" if pnl > 0 else "🔴 虧損" if pnl < 0 else "⚪ 打平"
        return f"損益：{pnl:+.4f} USDT{pct_text}　{indicator}"
    return None


def _format_duration_line(payload: dict[str, Any]) -> str | None:
    entry_time = payload.get("entry_time")
    exit_time = payload.get("exit_time")
    if not entry_time or not exit_time:
        return None
    try:
        started = datetime.fromisoformat(str(entry_time))
        ended = datetime.fromisoformat(str(exit_time))
    except ValueError:
        return None
    seconds = (ended - started).total_seconds()
    if seconds < 0:
        return None
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    return f"持倉時間：{hours}h {minutes}m"


def format_lifecycle_message(
    category: str,
    event_type: str,
    payload: dict[str, Any],
) -> str:
    """Format only operator-safe identifiers and evidence fields."""
    labels = (
        ("strategy_name", "策略"),
        ("strategy_id", "策略 ID"),
        ("position_id", "部位 ID"),
        ("inst_id", "商品"),
        ("instrument", "商品"),
        ("pair", "商品"),
        ("side", "方向"),
        ("quantity", "數量"),
        ("remaining_quantity", "剩餘數量"),
        ("entry_price", "進場價"),
        ("exit_price", "出場價"),
        ("current_price", "出場價"),
        ("fee", "手續費"),
        ("execution_status", "結果"),
        ("result", "結果"),
        ("reason", "原因"),
        ("exit_reason", "原因"),
        ("error", "錯誤"),
        ("errors", "錯誤"),
        ("pending_entries", "待處理進場"),
        ("cancellations_requested", "已請求取消"),
        ("correlation_id", "關聯 ID"),
    )
    lines = [f"Maybech｜{category}", f"事件：{event_type}"]
    pnl_line = _format_pnl_line(payload)
    if pnl_line:
        lines.append(pnl_line)
    used_labels: set[str] = set()
    for key, label in labels:
        value = payload.get(key)
        if isinstance(value, list):
            if not value:
                continue
            value = "、".join(str(item) for item in value)
        if value in (None, "") or label in used_labels:
            continue
        used_labels.add(label)
        if key == "fee":
            fee_currency = payload.get("fee_currency")
            value = f"{value} {fee_currency}" if fee_currency else value
        lines.append(f"{label}：{value}")
    duration_line = _format_duration_line(payload)
    if duration_line:
        lines.append(duration_line)
    return "\n".join(lines)


class LifecycleNotificationService(DaemonService):
    """Poll durable lifecycle audits and route only the supported categories."""

    name = "lifecycle_notifications"
    interval = 2.0
    _consumer = "lifecycle_notifications"

    def __init__(
        self,
        *,
        audit_store: AuditEventStore,
        line: LineBotNotifier | None = None,
        email: EmailNotifier | None = None,
        retry_base_seconds: float = 5,
        retry_max_seconds: float = 900,
        max_event_age_seconds: float = 900,
    ) -> None:
        super().__init__()
        self.audit_store = audit_store
        self.line = line or LineBotNotifier()
        self.email = email or EmailNotifier()
        self.retry_base_seconds = max(0, retry_base_seconds)
        self.retry_max_seconds = max(self.retry_base_seconds, retry_max_seconds)
        # Operator alerts are only news while they're fresh: after a backlog
        # stall (channel outage, backoff, process downtime), delivering
        # hours-old events reads as live spam about problems that may already
        # be fixed. Anything older than this at delivery time is dropped —
        # the durable audit trail still has it; only the push is skipped.
        self.max_event_age_seconds = max(0, max_event_age_seconds)
        self._unsubscribe: Callable[[], None] | None = None

    def setup(self) -> None:
        self.audit_store.initialize_delivery_cursor(self._consumer)
        if self.runtime is not None:
            self._unsubscribe = self.runtime.events.subscribe(self._on_runtime_event)

    def tick(self) -> None:
        for event in self.audit_store.list_after_delivery_cursor(self._consumer):
            if not self._deliver(event):
                break
            self.audit_store.advance_delivery_cursor(self._consumer, event)

    def teardown(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _is_stale(self, event: AuditEventRecord) -> bool:
        """True when the event is too old to still be operator news."""
        try:
            created = datetime.fromisoformat(event.created_at)
        except ValueError:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - created).total_seconds()
        return age > self.max_event_age_seconds

    def _on_runtime_event(self, event: RuntimeEvent) -> None:
        if event.type == "service.error":
            payload = {**event.payload, "service": event.source}
            self.audit_store.create(
                id=event.id,
                type="runtime.safety_failure",
                source=event.source,
                payload=payload,
                created_at=event.created_at.isoformat(),
            )

    def _deliver(self, event: AuditEventRecord) -> bool:
        event_type = event.type
        payload = event.payload
        category = (
            "執行環境／安全失敗"
            if event_type == "runtime.safety_failure"
            else classify_lifecycle_event(event_type, payload)
        )
        if category is None:
            return True
        if self._is_stale(event):
            return True
        message = format_lifecycle_message(category, event_type, payload)
        acknowledged = self.audit_store.acknowledged_delivery_channels(
            self._consumer, event.id
        )
        channels = (
            ("line", self.line, (message,)),
            ("email", self.email, (f"Maybech｜{category}", message)),
        )
        delivered = True
        for channel, notifier, args in channels:
            if channel in acknowledged or getattr(notifier, "enabled", True) is False:
                continue
            if not self.audit_store.notification_channel_retry_ready(
                self._consumer, channel
            ):
                delivered = False
                continue
            succeeded = notifier.send(*args)
            if not succeeded and getattr(notifier, "suppressed_by_cooldown", False):
                # A deliberate content-repeat/mute skip, not a transport failure:
                # acknowledging it (without touching the retry-health backoff)
                # lets the cursor move past it instead of stalling the whole
                # backlog behind a duplicate the operator never wanted resent.
                self.audit_store.acknowledge_delivery_channel(
                    self._consumer, event.id, channel
                )
                continue
            self.audit_store.record_notification_delivery_attempt(
                self._consumer,
                channel,
                event_id=event.id,
                succeeded=succeeded,
                error=str(getattr(notifier, "last_error", "")),
                retry_base_seconds=self.retry_base_seconds,
                retry_max_seconds=self.retry_max_seconds,
            )
            if succeeded:
                self.audit_store.acknowledge_delivery_channel(
                    self._consumer, event.id, channel
                )
            else:
                delivered = False
        return delivered
