"""Cross-process ownership for one live Maybech database and OKX account."""

from __future__ import annotations

import hashlib
import json
import os
import socket
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable
from typing import BinaryIO
from uuid import uuid4

from src.daemon.service import DaemonService


class RuntimeLeaseConflictError(RuntimeError):
    """Raised when another process already owns a live runtime resource."""


@dataclass(frozen=True)
class RuntimeLeaseStatus:
    held: bool
    owner_id: str
    pid: int
    hostname: str
    database: str
    account_scope: str
    acquired_at: str
    lock_root: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass
class _HeldLock:
    resource: str
    path: Path
    handle: BinaryIO


def account_scope(*, flag: str, uid: str) -> str:
    """Return a non-secret stable scope for one demo/real OKX account."""
    if flag not in {"0", "1"}:
        raise ValueError("OKX flag must be '0' or '1'")
    if not uid.strip():
        raise ValueError("OKX account uid is required")
    return hashlib.sha256(f"{flag}:{uid.strip()}".encode("utf-8")).hexdigest()[:24]


def default_lock_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        return Path(local_app_data) / "Maybech" / "locks"
    return Path.home() / ".maybech" / "locks"


class RuntimeLease:
    """Hold non-expiring OS locks until orderly release or process death."""

    def __init__(
        self,
        *,
        db_path: str,
        account_scope_value: str = "",
        lock_root: str | Path | None = None,
    ) -> None:
        self.database = os.path.normcase(
            os.path.realpath(os.path.abspath(os.path.expanduser(db_path)))
        )
        self.account_scope = account_scope_value
        self.lock_root = Path(lock_root) if lock_root is not None else default_lock_root()
        self.owner_id = uuid4().hex
        self.pid = os.getpid()
        self.hostname = socket.gethostname()
        self.acquired_at = ""
        self._locks: list[_HeldLock] = []

    @property
    def held(self) -> bool:
        expected = 2 if self.account_scope else 1
        return len(self._locks) == expected

    def acquire(self) -> RuntimeLeaseStatus:
        if self.held:
            return self.status()
        self.lock_root.mkdir(parents=True, exist_ok=True)
        resources = {
            f"database:{self.database}": self._lock_path("database", self.database)
        }
        if self.account_scope:
            resources[f"account:{self.account_scope}"] = self._lock_path(
                "account", self.account_scope
            )
        try:
            held_resources = {held_lock.resource for held_lock in self._locks}
            for resource, path in sorted(resources.items()):
                if resource in held_resources:
                    continue
                self._locks.append(self._acquire_one(resource, path))
            self.acquired_at = datetime.now(timezone.utc).isoformat()
            metadata = self.status().to_dict()
            for held_lock in self._locks:
                self._write_metadata(held_lock.handle, metadata)
        except Exception:
            self.release()
            raise
        return self.status()

    def bind_account_scope(self, account_scope_value: str) -> RuntimeLeaseStatus:
        if not account_scope_value:
            raise ValueError("Live runtime account scope is required")
        if self.account_scope and self.account_scope != account_scope_value:
            raise RuntimeError("Runtime lease is already bound to another account")
        self.account_scope = account_scope_value
        return self.acquire()

    def release(self) -> None:
        first_error: OSError | None = None
        for held_lock in reversed(self._locks):
            try:
                self._unlock(held_lock.handle)
            except OSError as exc:
                first_error = first_error or exc
            finally:
                held_lock.handle.close()
        self._locks = []
        self.acquired_at = ""
        if first_error is not None:
            raise first_error

    def status(self) -> RuntimeLeaseStatus:
        return RuntimeLeaseStatus(
            held=self.held,
            owner_id=self.owner_id,
            pid=self.pid,
            hostname=self.hostname,
            database=self.database,
            account_scope=self.account_scope,
            acquired_at=self.acquired_at,
            lock_root=str(self.lock_root.resolve()),
        )

    def _lock_path(self, kind: str, value: str) -> Path:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]
        return self.lock_root / f"{kind}-{digest}.lock"

    def _acquire_one(self, resource: str, path: Path) -> _HeldLock:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        handle = os.fdopen(descriptor, "r+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            self._lock(handle)
        except OSError as exc:
            owner = self._read_metadata(handle)
            handle.close()
            detail = f"; owner={owner}" if owner else ""
            raise RuntimeLeaseConflictError(
                f"Live runtime resource is already leased: {resource}{detail}"
            ) from exc
        return _HeldLock(resource=resource, path=path, handle=handle)

    @staticmethod
    def _write_metadata(handle: BinaryIO, metadata: dict[str, object]) -> None:
        payload = json.dumps(metadata, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        handle.seek(1)
        handle.write(payload)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())

    @staticmethod
    def _read_metadata(handle: BinaryIO) -> dict[str, object] | None:
        try:
            handle.seek(1)
            payload = handle.read().decode("utf-8")
            value = json.loads(payload) if payload else None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _lock(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            return
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class RuntimeLeaseService(DaemonService):
    """Acquire the live runtime lease before any exchange-capable service."""

    name = "runtime_lease"
    interval = 60.0

    def __init__(
        self,
        lease: RuntimeLease,
        *,
        before_release: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self.lease = lease
        self.before_release = before_release

    def setup(self) -> None:
        status = self.lease.acquire().to_dict()
        if self.runtime is not None:
            self.runtime.set_value("runtime.lease", status)

    def tick(self) -> None:
        if not self.lease.held:
            if self.before_release is not None:
                self.before_release()
            raise RuntimeError("Live runtime lease was lost")
        if self.runtime is not None:
            self.runtime.set_value("runtime.lease", self.lease.status().to_dict())

    def teardown(self) -> None:
        if self.before_release is not None:
            self.before_release()
        self.lease.release()
        if self.runtime is not None:
            self.runtime.set_value("runtime.lease", self.lease.status().to_dict())
