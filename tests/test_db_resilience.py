"""Cross-launch database resilience: crashed migrations, foreign files, lost ledgers.

The daemon must be able to start against whatever state the SQLite file is in
after an unclean previous shutdown — a migration applied but never recorded,
a ledger wiped, a file replaced or corrupted, or a database written by a
newer build — and either recover the last status or fail closed with a clear
instruction, never wedge or silently overwrite prior state.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest

import src.daemon.runtime as runtime_module
from src.trading.account_risk import AccountRiskLimits, AccountRiskStore
from src.trading.sqlite_schema import (
    DatabaseHealthError,
    UnsupportedSchemaError,
    check_database_file,
)
from src.trading.strategy_store import StrategyStore
from src.trading.trade_store import TradeStore


def _delete_ledger_rows(
    db_path: str, component: str, versions: list[int] | None = None
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        if versions is None:
            conn.execute(
                "DELETE FROM schema_migrations WHERE component = ?", (component,)
            )
        else:
            conn.executemany(
                "DELETE FROM schema_migrations WHERE component = ? AND version = ?",
                [(component, version) for version in versions],
            )
        conn.commit()
    finally:
        conn.close()


def _record_ledger_row(db_path: str, component: str, version: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """INSERT OR IGNORE INTO schema_migrations
               (component, version, applied_at) VALUES (?, ?, '2099-01-01')""",
            (component, version),
        )
        conn.commit()
    finally:
        conn.close()


def _saved_limits(store: AccountRiskStore) -> AccountRiskLimits:
    return store.save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=Decimal("100"),
            max_total_exposure_usd=Decimal("500"),
            max_leverage=Decimal("3"),
            max_stop_loss_equity_pct=Decimal("5"),
            allowed_instruments=("BTC-USDT-SWAP",),
        )
    )


# -- Crash between applying a migration and recording its ledger row ---------


def test_account_risk_recovers_when_alter_committed_but_version_row_lost(tmp_path):
    db_path = str(tmp_path / "trades.db")
    _saved_limits(AccountRiskStore(db_path))
    # executescript commits mid-migration: a crash can leave the v3/v4 columns
    # present with the ledger rows missing. Startup must re-record, not crash
    # with "duplicate column name".
    _delete_ledger_rows(db_path, "account_risk", [3, 4])

    store = AccountRiskStore(db_path)

    assert store.applied_schema_versions() == [1, 2, 3, 4]
    limits = store.get()
    assert limits is not None
    assert limits.max_stop_loss_equity_pct == Decimal("5")


def test_strategy_store_recovers_when_pending_table_exists_but_version_row_lost(
    tmp_path,
):
    db_path = str(tmp_path / "trades.db")
    StrategyStore(db_path)
    # strategy_pending_executions already exists; the retry must not fail with
    # "table strategy_pending_executions already exists".
    _delete_ledger_rows(db_path, "strategies", [4])

    store = StrategyStore(db_path)

    assert store.applied_schema_versions() == [1, 2, 3, 4, 5]


def test_account_risk_survives_completely_lost_migration_ledger(tmp_path):
    db_path = str(tmp_path / "trades.db")
    saved = _saved_limits(AccountRiskStore(db_path))
    _delete_ledger_rows(db_path, "account_risk")

    store = AccountRiskStore(db_path)

    assert store.applied_schema_versions() == [1, 2, 3, 4]
    limits = store.get()
    assert limits is not None
    assert limits.max_order_notional_usd == saved.max_order_notional_usd
    assert limits.allowed_instruments == saved.allowed_instruments


# -- Database written by a newer build ----------------------------------------


@pytest.mark.parametrize(
    ("component", "store_factory"),
    [
        ("account_risk", AccountRiskStore),
        ("trade_store", TradeStore),
        ("strategies", StrategyStore),
    ],
)
def test_store_refuses_database_from_newer_build(tmp_path, component, store_factory):
    db_path = str(tmp_path / "trades.db")
    store_factory(db_path)
    _record_ledger_row(db_path, component, 99)

    with pytest.raises(UnsupportedSchemaError, match="schema version 99"):
        store_factory(db_path)


# -- Database file health check ------------------------------------------------


def test_check_database_file_missing_reports_fresh_start_without_creating(tmp_path):
    path = tmp_path / "gone.db"

    result = check_database_file(str(path))

    assert result["status"] == "missing"
    assert not path.exists()


def test_check_database_file_empty_is_a_fresh_start(tmp_path):
    path = tmp_path / "empty.db"
    path.write_bytes(b"")

    assert check_database_file(str(path))["status"] == "empty"


def test_check_database_file_accepts_a_healthy_database(tmp_path):
    db_path = str(tmp_path / "trades.db")
    TradeStore(db_path)

    result = check_database_file(db_path)

    assert result["status"] == "ok"
    assert result["size_bytes"] > 0


def test_check_database_file_rejects_a_non_sqlite_file_without_touching_it(tmp_path):
    path = tmp_path / "trades.db"
    original = b"definitely not a sqlite database, maybe a stray log file"
    path.write_bytes(original)

    with pytest.raises(DatabaseHealthError, match="not a SQLite database"):
        check_database_file(str(path))
    assert path.read_bytes() == original


def test_check_database_file_rejects_a_corrupt_database(tmp_path):
    path = tmp_path / "trades.db"
    # Valid header followed by garbage: SQLite accepts the file type but the
    # page structure is destroyed.
    path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)

    with pytest.raises(DatabaseHealthError, match="integrity check"):
        check_database_file(str(path))


def test_runner_fails_closed_on_corrupt_database_before_any_store(
    monkeypatch, tmp_path
):
    path = tmp_path / "trades.db"
    path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
    monkeypatch.setattr(
        runtime_module,
        "activate_db_path",
        lambda mode: {
            "configured_path": str(path),
            "resolved_path": str(path),
            "source": "test",
            "env_key": "MAYBECH_DB_PATH",
            "existed_before_process": True,
        },
    )

    def forbidden_store(*args, **kwargs):
        raise AssertionError("no store may open a database that failed health checks")

    monkeypatch.setattr(runtime_module, "TradeStore", forbidden_store)

    with pytest.raises(DatabaseHealthError, match="integrity check"):
        runtime_module.create_default_runner(mode="simulation")
