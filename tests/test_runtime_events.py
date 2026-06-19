from src.daemon.events import EventBus, RuntimeState
from src.daemon.service import DaemonRunner, DaemonService


class EventMockService(DaemonService):
    name = "event_mock"
    interval = 1.0

    def setup(self):
        pass

    def tick(self):
        self.publish_event("mock.tick", {"ok": True})


def test_event_bus_publishes_to_history_and_subscribers():
    bus = EventBus(max_events=2)
    received = []
    unsubscribe = bus.subscribe(received.append)

    first = bus.publish("alpha", "test", {"value": 1})
    bus.publish("beta", "test", {"value": 2})
    bus.publish("alpha", "test", {"value": 3})

    unsubscribe()
    bus.publish("alpha", "test", {"value": 4})

    assert received[0] == first
    assert len(received) == 3
    assert [event.payload["value"] for event in bus.recent()] == [3, 4]
    assert [event.payload["value"] for event in bus.recent(event_type="beta")] == []


def test_runner_attaches_runtime_and_records_service_state():
    runtime = RuntimeState()
    runner = DaemonRunner(runtime=runtime)
    service = EventMockService()

    runner.register(service)
    runner.disable_service("event_mock")

    status = runtime.get_service("event_mock")
    event_types = [event.type for event in runtime.events.recent()]

    assert service.runtime is runtime
    assert status["active"] is False
    assert "service.registered" in event_types
    assert "service.disabled" in event_types


def test_service_can_publish_domain_event_through_runtime():
    runtime = RuntimeState()
    runner = DaemonRunner(runtime=runtime)
    service = EventMockService()

    runner.register(service)
    service.tick()

    events = runtime.events.recent(event_type="mock.tick")
    assert len(events) == 1
    assert events[0].source == "event_mock"
    assert events[0].payload == {"ok": True}


def test_runtime_state_stores_named_snapshots():
    runtime = RuntimeState()
    decisions = [{"allowed": False, "reason": "blocked"}]

    runtime.set_value("strategy.decisions", decisions)
    decisions.append({"allowed": True, "reason": "mutated"})

    stored = runtime.get_value("strategy.decisions")
    assert stored == [{"allowed": False, "reason": "blocked"}]
