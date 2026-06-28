import subprocess
import sys

import pytest

from src.runtime.lease import (
    RuntimeLease,
    RuntimeLeaseConflictError,
    RuntimeLeaseService,
    account_scope,
)


def test_account_scope_separates_demo_and_real_without_exposing_uid():
    demo = account_scope(flag="1", uid="123456")
    real = account_scope(flag="0", uid="123456")

    assert demo != real
    assert len(demo) == 24
    assert "123456" not in demo


def test_runtime_lease_excludes_same_database_or_account(tmp_path):
    lock_root = tmp_path / "locks"
    first = RuntimeLease(
        db_path=str(tmp_path / "first.db"),
        account_scope_value="account-a",
        lock_root=lock_root,
    )
    same_database = RuntimeLease(
        db_path=str(tmp_path / "first.db"),
        account_scope_value="account-b",
        lock_root=lock_root,
    )
    same_account = RuntimeLease(
        db_path=str(tmp_path / "second.db"),
        account_scope_value="account-a",
        lock_root=lock_root,
    )
    unrelated = RuntimeLease(
        db_path=str(tmp_path / "second.db"),
        account_scope_value="account-b",
        lock_root=lock_root,
    )
    first.acquire()

    with pytest.raises(RuntimeLeaseConflictError, match="already leased"):
        same_database.acquire()
    with pytest.raises(RuntimeLeaseConflictError, match="already leased"):
        same_account.acquire()
    assert unrelated.acquire().held is True

    unrelated.release()
    first.release()
    assert same_database.acquire().held is True
    same_database.release()


def test_runtime_lease_blocks_another_process_and_releases_cleanly(tmp_path):
    lock_root = tmp_path / "locks"
    db_path = tmp_path / "runtime.db"
    lease = RuntimeLease(
        db_path=str(db_path),
        account_scope_value="account-a",
        lock_root=lock_root,
    )
    lease.acquire()
    script = (
        "import sys; "
        "from src.runtime.lease import RuntimeLease, RuntimeLeaseConflictError; "
        "lease=RuntimeLease(db_path=sys.argv[1], account_scope_value=sys.argv[2], "
        "lock_root=sys.argv[3]); "
        "\ntry:\n lease.acquire()\nexcept RuntimeLeaseConflictError:\n sys.exit(3)\n"
        "lease.release()"
    )
    command = [
        sys.executable,
        "-c",
        script,
        str(db_path),
        "account-a",
        str(lock_root),
    ]

    blocked = subprocess.run(command, check=False, timeout=10)
    lease.release()
    acquired = subprocess.run(command, check=False, timeout=10)

    assert blocked.returncode == 3
    assert acquired.returncode == 0


def test_runtime_lease_service_disarms_before_releasing(tmp_path):
    lease = RuntimeLease(
        db_path=str(tmp_path / "runtime.db"),
        account_scope_value="account-a",
        lock_root=tmp_path / "locks",
    )
    observed = []
    service = RuntimeLeaseService(
        lease,
        before_release=lambda: observed.append(lease.held),
    )

    service.setup()
    service.teardown()

    assert observed == [True]
    assert lease.held is False
