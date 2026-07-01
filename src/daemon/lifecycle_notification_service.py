"""Deliver a bounded set of operator lifecycle events to configured channels."""

from __future__ import annotations

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
    "position.filled_without_allocation": "成交尚未分配",
    "execution.fill_rejected": "成交資料遭拒絕",
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
        ("execution_status", "結果"),
        ("result", "結果"),
        ("reason", "原因"),
        ("error", "錯誤"),
        ("correlation_id", "關聯 ID"),
    )
    lines = [f"Maybech｜{category}", f"事件：{event_type}"]
    used_labels: set[str] = set()
    for key, label in labels:
        value = payload.get(key)
        if value in (None, "") or label in used_labels:
            continue
        used_labels.add(label)
        lines.append(f"{label}：{value}")
    return "\n".join(lines)


class LifecycleNotificationService(DaemonService):
    """Poll durable lifecycle audits and route only the supported categories."""

    name = "lifecycle_notifications"
    interval = 2.0

    def __init__(
        self,
        *,
        audit_store: AuditEventStore,
        line: LineBotNotifier | None = None,
        email: EmailNotifier | None = None,
    ) -> None:
        super().__init__()
        self.audit_store = audit_store
        self.line = line or LineBotNotifier()
        self.email = email or EmailNotifier()
        self._seen_ids: set[str] = set()
        self._unsubscribe: Callable[[], None] | None = None

    def setup(self) -> None:
        self._seen_ids = {
            event.id for event in self.audit_store.list(limit=500)
        }
        if self.runtime is not None:
            self._unsubscribe = self.runtime.events.subscribe(self._on_runtime_event)

    def tick(self) -> None:
        events = self.audit_store.list(limit=500)
        for event in reversed(events):
            if event.id in self._seen_ids:
                continue
            self._seen_ids.add(event.id)
            self._deliver(event.type, event.payload)
        if len(self._seen_ids) > 2000:
            current = {event.id for event in events}
            self._seen_ids.intersection_update(current)

    def teardown(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

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

    def _deliver(self, event_type: str, payload: dict[str, Any]) -> None:
        category = (
            "執行環境／安全失敗"
            if event_type == "runtime.safety_failure"
            else classify_lifecycle_event(event_type, payload)
        )
        if category is None:
            return
        message = format_lifecycle_message(category, event_type, payload)
        self.line.send(message)
        self.email.send(f"Maybech｜{category}", message)
