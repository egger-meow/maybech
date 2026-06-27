"""
Daemon framework for running multiple background services.
"""

import time
import traceback
from abc import ABC, abstractmethod
from typing import Callable, Dict, Optional
from datetime import datetime

from src.daemon.events import RuntimeState
from src.utils.logger import setup_logger

logger = setup_logger(__name__)


class DaemonService(ABC):
    """Base class for background services."""

    name: str
    interval: float  # seconds

    def __init__(self) -> None:
        self.active = True
        self.last_tick: Optional[float] = None
        self.last_run_time: Optional[float] = None
        self.error_count = 0
        self.runtime: Optional[RuntimeState] = None

    def publish_event(self, event_type: str, payload: Optional[dict] = None) -> None:
        """Publish a service event when a runtime state is attached."""
        if self.runtime is not None:
            self.runtime.events.publish(event_type, self.name, payload or {})

    @abstractmethod
    def setup(self) -> None:
        """Initialise service-specific components."""
        pass

    @abstractmethod
    def tick(self) -> None:
        """The main work loop of the service."""
        pass

    def teardown(self) -> None:
        """Cleanup before stopping."""
        pass


class DaemonRunner:
    """Manages and executes multiple DaemonServices."""

    def __init__(self, runtime: Optional[RuntimeState] = None) -> None:
        self.services: Dict[str, DaemonService] = {}
        self.runtime = runtime or RuntimeState()
        self.running = False
        self._setup_complete = False
        self._shutdown_callbacks: list[Callable[[], None]] = []

    def register(self, service: DaemonService) -> None:
        """Register a new service."""
        if service.name in self.services:
            raise ValueError(f"Service '{service.name}' is already registered.")
        service.runtime = self.runtime
        self.services[service.name] = service
        self._record_service_status(service.name)
        self.runtime.events.publish(
            "service.registered",
            service.name,
            {"interval": service.interval, "active": service.active},
        )
        logger.info(f"Registered service: {service.name} (interval={service.interval}s)")

    def enable_service(self, name: str) -> bool:
        """Enable a registered service."""
        if name in self.services:
            self.services[name].active = True
            self._record_service_status(name)
            self.runtime.events.publish("service.enabled", name)
            logger.info(f"Service '{name}' enabled.")
            return True
        return False

    def disable_service(self, name: str) -> bool:
        """Disable a registered service."""
        if name in self.services:
            self.services[name].active = False
            self._record_service_status(name)
            self.runtime.events.publish("service.disabled", name)
            logger.info(f"Service '{name}' disabled.")
            return True
        return False

    def get_service_status(self, name: str) -> Optional[dict]:
        """Get the current status of a service for API/runtime readers."""
        if name not in self.services:
            return None
        
        svc = self.services[name]
        status = {
            "name": svc.name,
            "active": svc.active,
            "interval": svc.interval,
            "last_tick": datetime.fromtimestamp(svc.last_tick).strftime("%H:%M:%S") if svc.last_tick else None,
            "last_duration": f"{svc.last_run_time:.2f}s" if svc.last_run_time else None,
            "errors": svc.error_count
        }
        self.runtime.update_service(name, status)
        return status

    def _record_service_status(self, name: str) -> None:
        self.get_service_status(name)

    def add_shutdown_callback(self, callback: Callable[[], None]) -> None:
        self._shutdown_callbacks.append(callback)

    def setup_services(self, *, required_services: set[str] | None = None) -> None:
        """Set up services once, optionally failing startup for required ones."""
        if self._setup_complete:
            return
        required = required_services or set()
        for name, svc in self.services.items():
            try:
                svc.setup()
                self._record_service_status(name)
                self.runtime.events.publish("service.setup", name)
            except Exception as exc:
                logger.error(f"Setup failed for service '{name}': {exc}")
                svc.active = False
                svc.error_count += 1
                self._record_service_status(name)
                self.runtime.events.publish(
                    "service.error",
                    name,
                    {"stage": "setup", "error": str(exc)},
                )
                if name in required:
                    self.teardown_services()
                    raise RuntimeError(
                        f"Required service '{name}' failed setup: {exc}"
                    ) from exc
        self._setup_complete = True

    def teardown_services(self) -> None:
        """Tear down services and run process-safety callbacks."""
        for name, svc in reversed(list(self.services.items())):
            try:
                svc.teardown()
            except Exception as exc:
                logger.error(f"Teardown failed for service '{name}': {exc}")
        callbacks = self._shutdown_callbacks
        self._shutdown_callbacks = []
        for callback in reversed(callbacks):
            try:
                callback()
            except Exception as exc:
                logger.error(f"Shutdown callback failed: {exc}")
        self._setup_complete = False

    def run_forever(self) -> None:
        """Run the main loop across all registered services."""
        logger.info("Starting DaemonRunner main loop...")

        try:
            self.setup_services()
            self.running = True
            while self.running:
                now = time.time()
                for name, svc in self.services.items():
                    if not svc.active:
                        continue
                    
                    # Check if it's time to tick
                    if svc.last_tick is None or (now - svc.last_tick) >= svc.interval:
                        try:
                            start_time = time.time()
                            svc.tick()
                            svc.last_run_time = time.time() - start_time
                            svc.last_tick = time.time()
                            svc.error_count = 0
                            self._record_service_status(name)
                            self.runtime.events.publish(
                                "service.tick",
                                name,
                                {"duration": svc.last_run_time},
                            )
                        except Exception as e:
                            svc.error_count += 1
                            logger.error(f"Error in service '{name}': {e}")
                            logger.debug(traceback.format_exc())
                            self._record_service_status(name)
                            self.runtime.events.publish("service.error", name, {"stage": "tick", "error": str(e)})
                
                # Sleep briefly to avoid 100% CPU usage
                time.sleep(1)
                
        except KeyboardInterrupt:
            logger.info("DaemonRunner stopping (KeyboardInterrupt)...")
        finally:
            self.running = False
            self.teardown_services()
