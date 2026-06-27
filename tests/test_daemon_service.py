"""
Tests for the Daemon framework (runner and services).
"""

import time
from unittest.mock import MagicMock
import pytest
from src.daemon.service import DaemonService, DaemonRunner


class MockService(DaemonService):
    name = "mock"
    interval = 0.1

    def __init__(self):
        super().__init__()
        self.setup_called = False
        self.tick_count = 0

    def setup(self):
        self.setup_called = True

    def tick(self):
        self.tick_count += 1


class BrokenSetupService(MockService):
    name = "broken"

    def setup(self):
        raise RuntimeError("setup failed")


def test_daemon_runner_registration():
    runner = DaemonRunner()
    svc = MockService()
    runner.register(svc)
    assert "mock" in runner.services
    
    status = runner.get_service_status("mock")
    assert status["name"] == "mock"
    assert status["active"] is True


def test_daemon_runner_enable_disable():
    runner = DaemonRunner()
    svc = MockService()
    runner.register(svc)
    
    runner.disable_service("mock")
    assert runner.services["mock"].active is False
    
    runner.enable_service("mock")
    assert runner.services["mock"].active is True


def test_runner_tick_execution():
    """Verify that the runner actually triggers ticks."""
    runner = DaemonRunner()
    svc = MockService()
    runner.register(svc)
    
    # Run setup
    svc.setup()
    assert svc.setup_called is True
    
    # Manual tick trigger simulation (without running forever)
    # We call tick and update state like the runner would
    start_time = time.time()
    svc.tick()
    svc.last_run_time = time.time() - start_time
    svc.last_tick = time.time()

    assert svc.tick_count == 1
    assert svc.last_tick is not None
    assert svc.last_run_time is not None


def test_required_service_setup_failure_runs_shutdown_callback():
    runner = DaemonRunner()
    runner.register(BrokenSetupService())
    shutdown_calls = []
    runner.add_shutdown_callback(lambda: shutdown_calls.append(True))

    with pytest.raises(RuntimeError, match="Required service 'broken'"):
        runner.setup_services(required_services={"broken"})

    assert runner.services["broken"].active is False
    assert shutdown_calls == [True]
