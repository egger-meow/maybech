"""
In-memory runtime event stream for daemon services.

This module is intentionally dependency-free so it can be used by the current
HTTP/WebSocket API and background services without coupling components.
"""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable, Deque, Optional
from uuid import uuid4


@dataclass(frozen=True)
class RuntimeEvent:
    """A normalized event emitted by daemon services."""

    type: str
    source: str
    payload: dict
    id: str = field(default_factory=lambda: uuid4().hex)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


Subscriber = Callable[[RuntimeEvent], None]


class EventBus:
    """Thread-safe bounded event stream with optional live subscribers."""

    def __init__(self, max_events: int = 500) -> None:
        self._events: Deque[RuntimeEvent] = deque(maxlen=max_events)
        self._subscribers: list[Subscriber] = []
        self._lock = RLock()

    def publish(self, event_type: str, source: str, payload: Optional[dict] = None) -> RuntimeEvent:
        event = RuntimeEvent(
            type=event_type,
            source=source,
            payload=payload or {},
        )
        with self._lock:
            self._events.append(event)
            subscribers = list(self._subscribers)

        for subscriber in subscribers:
            subscriber(event)
        return event

    def subscribe(self, subscriber: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subscribers.append(subscriber)

        def unsubscribe() -> None:
            with self._lock:
                if subscriber in self._subscribers:
                    self._subscribers.remove(subscriber)

        return unsubscribe

    def recent(self, limit: int = 100, event_type: Optional[str] = None) -> list[RuntimeEvent]:
        with self._lock:
            events = list(self._events)

        if event_type is not None:
            events = [event for event in events if event.type == event_type]
        return events[-limit:]


class RuntimeState:
    """Shared runtime state for services and UI/API readers."""

    def __init__(self, event_bus: Optional[EventBus] = None) -> None:
        self.events = event_bus or EventBus()
        self._services: dict[str, dict] = {}
        self._values: dict[str, Any] = {}
        self._lock = RLock()

    def update_service(self, name: str, status: dict) -> None:
        with self._lock:
            self._services[name] = dict(status)

    def get_service(self, name: str) -> Optional[dict]:
        with self._lock:
            status = self._services.get(name)
            return dict(status) if status is not None else None

    def list_services(self) -> dict[str, dict]:
        with self._lock:
            return {name: dict(status) for name, status in self._services.items()}

    def set_value(self, key: str, value: Any) -> None:
        with self._lock:
            self._values[key] = deepcopy(value)

    def get_value(self, key: str) -> Any:
        with self._lock:
            value = self._values.get(key)
            return deepcopy(value) if value is not None else None
