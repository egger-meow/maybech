from decimal import Decimal
import sqlite3

import pytest

from src.trading.account_risk import (
    AccountRiskGuard,
    AccountRiskLimits,
    AccountRiskStore,
    EntryRiskBlocked,
)
from src.trading.logical_position_store import (
    LogicalPositionProtection,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.sqlite_schema import configure_connection, initialize_schema, record_schema_version


class RiskClient:
    def __init__(self):
        self.total_equity = "1000"
        self.positions = [
            {
                "instId": "BTC-USDT-SWAP",
                "pos": "-1",
                "notionalUsd": "100",
                "markPx": "100",
            }
        ]
        self.pending = [
            {
                "instId": "ETH-USDT-SWAP",
                "sz": "3",
                "accFillSz": "1",
                "px": "1900",
                "reduceOnly": "false",
            },
            {
                "instId": "ETH-USDT-SWAP",
                "sz": "10",
                "accFillSz": "0",
                "px": "1900",
                "reduceOnly": "true",
            },
        ]
        self.leverage = "5"
        self.pending_algos = [
            {
                "algoId": "algo-stop",
                "algoClOrdId": "stopclient",
                "instId": "BTC-USDT-SWAP",
                "side": "buy",
                "ordType": "conditional",
                "state": "live",
                "posSide": "net",
                "reduceOnly": "true",
                "sz": "1",
                "slTriggerPx": "110",
                "slOrdPx": "-1",
            }
        ]

    def get_instruments(self, *, inst_type, inst_id):
        return [
            {
                "instId": inst_id,
                "instType": inst_type,
                "state": "live",
                "ctType": "linear",
                "settleCcy": "USDT",
                "ctVal": "0.01",
            }
        ]

    def get_leverage(self, *, inst_id, mgn_mode):
        return [{"instId": inst_id, "mgnMode": mgn_mode, "lever": self.leverage}]

    def get_balance(self):
        return [{"totalEq": self.total_equity}]

    def get_positions(self, *, inst_type):
        return self.positions

    def get_pending_orders(self, *, inst_type):
        return self.pending

    def get_pending_algo_orders(self, *, inst_id):
        return [item for item in self.pending_algos if item["instId"] == inst_id]


def _store(
    tmp_path,
    *,
    order="50",
    total="200",
    leverage="10",
    stop_budget="10",
    enabled=True,
):
    store = AccountRiskStore(str(tmp_path / "trades.db"))
    store.save(
        AccountRiskLimits(
            enabled=enabled,
            max_order_notional_usd=Decimal(order),
            max_total_exposure_usd=Decimal(total),
            max_leverage=Decimal(leverage),
            max_stop_loss_equity_pct=Decimal(stop_budget),
            allowed_instruments=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        )
    )
    store.set_entries_enabled(True)
    position_store = LogicalPositionStore(store.db_path)
    position_store.save(
        LogicalPositionRecord(
            id="btc-short",
            inst_id="BTC-USDT-SWAP",
            side="short",
            opened_quantity=1,
            remaining_quantity=1,
            entry_price=100,
        )
    )
    position_store.save_protection(
        LogicalPositionProtection(
            position_id="btc-short",
            kind="standalone_stop",
            algo_id="algo-stop",
            algo_client_order_id="stopclient",
            quantity=1,
            stop_loss=110,
        )
    )
    return store


def test_account_risk_store_persists_one_versioned_envelope(tmp_path):
    store = _store(tmp_path)

    limits = AccountRiskStore(store.db_path).get()

    assert limits is not None
    assert limits.enabled is True
    assert limits.max_order_notional_usd == Decimal("50")
    assert limits.max_total_exposure_usd == Decimal("200")
    assert limits.max_leverage == Decimal("10")
    assert limits.max_stop_loss_equity_pct == Decimal("10")
    assert limits.allowed_instruments == ("BTC-USDT-SWAP", "ETH-USDT-SWAP")
    assert limits.entries_enabled is True
    assert store.applied_schema_versions() == [1, 2, 3, 4]


def test_account_risk_store_migrates_v2_envelope_to_instrument_allowlist(tmp_path):
    db_path = str(tmp_path / "risk-v2.db")
    conn = sqlite3.connect(db_path)
    try:
        configure_connection(conn)
        initialize_schema(
            conn,
            schema_sql="""
                CREATE TABLE account_risk_limits (
                    id TEXT PRIMARY KEY CHECK (id = 'account'),
                    enabled INTEGER NOT NULL DEFAULT 0,
                    max_order_notional_usd TEXT NOT NULL,
                    max_total_exposure_usd TEXT NOT NULL,
                    max_leverage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            """,
            component="account_risk",
            version=1,
        )
        conn.execute(
            """CREATE TABLE entry_control (
                id TEXT PRIMARY KEY CHECK (id = 'account'),
                entries_enabled INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            """INSERT INTO account_risk_limits
               VALUES ('account', 0, '100', '500', '5', 'before', 'before')"""
        )
        conn.execute("INSERT INTO entry_control VALUES ('account', 0, 'before')")
        record_schema_version(conn, component="account_risk", version=2)
        conn.commit()
    finally:
        conn.close()

    store = AccountRiskStore(db_path)
    migrated = store.get()

    assert store.applied_schema_versions() == [1, 2, 3, 4]
    assert migrated is not None
    assert migrated.allowed_instruments == ()
    # Migrated rows stay readable but the zero stop-loss budget must not
    # validate, so entries and preflight remain blocked until it is set.
    assert migrated.max_stop_loss_equity_pct == Decimal("0")
    with pytest.raises(ValueError, match="max_stop_loss_equity_pct"):
        migrated.validate()


def test_entry_approval_blocks_instrument_outside_account_allowlist(tmp_path):
    store = _store(tmp_path)
    current = store.get()
    assert current is not None
    store.set_entries_enabled(False)
    store.save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=current.max_order_notional_usd,
            max_total_exposure_usd=current.max_total_exposure_usd,
            max_leverage=current.max_leverage,
            max_stop_loss_equity_pct=current.max_stop_loss_equity_pct,
            allowed_instruments=("BTC-USDT-SWAP",),
        )
    )
    store.set_entries_enabled(True)

    with pytest.raises(EntryRiskBlocked, match="outside the account risk allowlist"):
        AccountRiskGuard(RiskClient(), store).approve_entry(
            inst_id="ETH-USDT-SWAP",
            side="long",
            requested_size="1",
            entry_price="2000",
            stop_loss_price="1900",
        )


def test_entry_approval_counts_positions_pending_entries_and_requested_order(tmp_path):
    approval = AccountRiskGuard(RiskClient(), _store(tmp_path)).approve_entry(
        inst_id="ETH-USDT-SWAP",
        side="long",
        requested_size="2",
        entry_price="2000",
        stop_loss_price="1900",
    )

    assert approval.order_notional_usd == Decimal("40.00")
    assert approval.existing_exposure_usd == Decimal("138.00")
    assert approval.projected_exposure_usd == Decimal("178.00")
    assert approval.leverage == Decimal("5")
    # Candidate: |2000-1900| x 2 contracts x 0.01 ctVal = 2 USD.
    # Existing short unit: stop 110 vs mark 100 x 1 contract x 0.01 = 0.1 USD.
    assert approval.stop_loss_price == Decimal("1900")
    assert approval.worst_case_loss_usd == Decimal("2")
    assert approval.existing_worst_case_loss_usd == Decimal("0.1")
    assert approval.projected_worst_case_loss_usd == Decimal("2.1")
    assert approval.equity_usd == Decimal("1000")


@pytest.mark.parametrize(
    ("store_kwargs", "client_change", "expected"),
    [
        ({"order": "30"}, None, "order notional"),
        ({"total": "170"}, None, "projected exposure"),
        ({"leverage": "4"}, ("leverage", "5"), "configured leverage"),
        # Projected all-stop loss is 2.1 USD; 0.2% of 1000 equity = 2 USD.
        ({"stop_budget": "0.2"}, None, "all-stop loss"),
        ({}, ("total_equity", "20"), "all-stop loss"),
        ({"enabled": False}, None, "limits are disabled"),
    ],
)
def test_entry_approval_blocks_every_risk_limit(
    tmp_path, store_kwargs, client_change, expected
):
    client = RiskClient()
    if client_change:
        setattr(client, *client_change)

    with pytest.raises(EntryRiskBlocked, match=expected):
        AccountRiskGuard(client, _store(tmp_path, **store_kwargs)).approve_entry(
            inst_id="ETH-USDT-SWAP",
            side="long",
            requested_size="2",
            entry_price="2000",
            stop_loss_price="1900",
        )


def test_entry_approval_fails_closed_when_exchange_exposure_is_incomplete(tmp_path):
    client = RiskClient()
    client.positions = [
        {
            "instId": "BTC-USDT-SWAP",
            "pos": "-1",
            "notionalUsd": "",
            "markPx": "100",
        }
    ]

    with pytest.raises(EntryRiskBlocked, match="has no notionalUsd"):
        AccountRiskGuard(client, _store(tmp_path)).approve_entry(
            inst_id="ETH-USDT-SWAP",
            side="long",
            requested_size="1",
            entry_price="2000",
            stop_loss_price="1900",
        )


def test_entry_approval_blocks_unexplained_exchange_exposure(tmp_path):
    client = RiskClient()
    client.positions.append(
        {"instId": "ETH-USDT-SWAP", "pos": "2", "notionalUsd": "40"}
    )

    with pytest.raises(EntryRiskBlocked, match="does not reconcile"):
        AccountRiskGuard(client, _store(tmp_path)).approve_entry(
            inst_id="ETH-USDT-SWAP",
            side="long",
            requested_size="1",
            entry_price="2000",
            stop_loss_price="1900",
        )


def test_entry_approval_rechecks_owned_stop_on_okx(tmp_path):
    client = RiskClient()
    client.positions = [
        {
            "instId": "BTC-USDT-SWAP",
            "pos": "-1",
            "notionalUsd": "100",
            "markPx": "100",
        }
    ]
    client.pending_algos = []
    store = _store(tmp_path)

    with pytest.raises(EntryRiskBlocked, match="protection is not active"):
        AccountRiskGuard(client, store).approve_entry(
            inst_id="ETH-USDT-SWAP",
            side="long",
            requested_size="1",
            entry_price="2000",
            stop_loss_price="1900",
        )

    client.pending_algos = [
        {
            "algoId": "algo-stop",
            "algoClOrdId": "stopclient",
            "instId": "BTC-USDT-SWAP",
            "side": "buy",
            "ordType": "conditional",
            "state": "live",
            "posSide": "net",
            "reduceOnly": "true",
            "sz": "1",
            "slTriggerPx": "110",
            "slOrdPx": "-1",
        }
    ]
    approval = AccountRiskGuard(client, store).approve_entry(
        inst_id="ETH-USDT-SWAP",
        side="long",
        requested_size="1",
        entry_price="2000",
        stop_loss_price="1900",
    )
    assert approval.inst_id == "ETH-USDT-SWAP"


def test_preview_envelope_usage_matches_verified_sum(tmp_path):
    preview = AccountRiskGuard(RiskClient(), _store(tmp_path)).preview_envelope_usage()

    assert preview.equity_usd == Decimal("1000")
    assert preview.max_stop_loss_equity_pct == Decimal("10")
    assert preview.loss_budget_usd == Decimal("100")
    assert preview.existing_worst_case_loss_usd == Decimal("0.1")
    assert preview.degraded is False


def test_preview_envelope_usage_degrades_instead_of_raising_on_unverifiable_protection(tmp_path):
    client = RiskClient()
    # No matching pending algo order on OKX for the stored protection, so
    # the strict path (approve_entry) would raise "protection is not
    # active" — the preview must instead exclude this unit and flag it.
    client.pending_algos = []
    preview = AccountRiskGuard(client, _store(tmp_path)).preview_envelope_usage()

    assert preview.equity_usd == Decimal("1000")
    assert preview.existing_worst_case_loss_usd == Decimal("0")
    assert preview.degraded is True


def test_entry_approval_rejects_protection_quantity_stale_from_logical_unit(tmp_path):
    client = RiskClient()
    client.positions = [
        {
            "instId": "BTC-USDT-SWAP",
            "pos": "-1",
            "notionalUsd": "100",
            "markPx": "100",
        }
    ]
    client.pending_algos = [
        {
            "algoId": "algo-stop",
            "algoClOrdId": "stopclient",
            "instId": "BTC-USDT-SWAP",
            "side": "buy",
            "ordType": "conditional",
            "state": "live",
            "posSide": "net",
            "reduceOnly": "true",
            "sz": "0.5",
            "slTriggerPx": "110",
            "slOrdPx": "-1",
        }
    ]
    store = _store(tmp_path)
    LogicalPositionStore(store.db_path).update_protection(
        "btc-short",
        quantity=0.5,
    )

    with pytest.raises(EntryRiskBlocked, match="quantity does not match"):
        AccountRiskGuard(client, store).approve_entry(
            inst_id="ETH-USDT-SWAP",
            side="long",
            requested_size="1",
            entry_price="2000",
            stop_loss_price="1900",
        )


def test_entry_approval_is_disabled_by_default_and_survives_restart(tmp_path):
    store = AccountRiskStore(str(tmp_path / "trades.db"))
    store.save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=Decimal("50"),
            max_total_exposure_usd=Decimal("200"),
            max_leverage=Decimal("10"),
            max_stop_loss_equity_pct=Decimal("10"),
            allowed_instruments=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        )
    )

    with pytest.raises(EntryRiskBlocked, match="disabled by the operator"):
        AccountRiskGuard(RiskClient(), AccountRiskStore(store.db_path)).approve_entry(
            inst_id="ETH-USDT-SWAP",
            side="long",
            requested_size="1",
            entry_price="2000",
            stop_loss_price="1900",
        )


@pytest.mark.parametrize(
    ("side", "stop", "expected"),
    [
        ("long", "2000", "stop loss below the long entry price"),
        ("long", "2100", "stop loss below the long entry price"),
        ("short", "2000", "stop loss above the short entry price"),
        ("short", "1900", "stop loss above the short entry price"),
        ("hold", "1900", "side must be 'long' or 'short'"),
    ],
)
def test_entry_approval_requires_side_consistent_stop_loss(
    tmp_path, side, stop, expected
):
    with pytest.raises(EntryRiskBlocked, match=expected):
        AccountRiskGuard(RiskClient(), _store(tmp_path)).approve_entry(
            inst_id="ETH-USDT-SWAP",
            side=side,
            requested_size="1",
            entry_price="2000",
            stop_loss_price=stop,
        )


def test_entry_approval_fails_closed_without_account_equity(tmp_path):
    client = RiskClient()
    client.total_equity = ""

    with pytest.raises(EntryRiskBlocked, match="totalEq"):
        AccountRiskGuard(client, _store(tmp_path)).approve_entry(
            inst_id="ETH-USDT-SWAP",
            side="long",
            requested_size="1",
            entry_price="2000",
            stop_loss_price="1900",
        )


def test_entry_approval_fails_closed_without_mark_price_for_open_unit(tmp_path):
    client = RiskClient()
    client.positions = [
        {"instId": "BTC-USDT-SWAP", "pos": "-1", "notionalUsd": "100"}
    ]

    with pytest.raises(EntryRiskBlocked, match="no mark price"):
        AccountRiskGuard(client, _store(tmp_path)).approve_entry(
            inst_id="ETH-USDT-SWAP",
            side="long",
            requested_size="1",
            entry_price="2000",
            stop_loss_price="1900",
        )


def test_stop_already_past_mark_never_offsets_other_units_risk(tmp_path):
    # The BTC short's stop trigger (110) is below the mark (120), i.e. the
    # stop is already breached and firing: its remaining loss contribution is
    # clamped to zero, never a credit. The candidate's own 2 USD loss must
    # still exhaust a 0.15% x 1000 = 1.5 USD budget and stay blocked.
    client = RiskClient()
    client.positions[0]["markPx"] = "120"

    guard = AccountRiskGuard(client, _store(tmp_path, stop_budget="0.15"))
    with pytest.raises(EntryRiskBlocked, match="all-stop loss"):
        guard.approve_entry(
            inst_id="ETH-USDT-SWAP",
            side="long",
            requested_size="2",
            entry_price="2000",
            stop_loss_price="1900",
        )
