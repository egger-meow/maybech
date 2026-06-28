from decimal import Decimal

import pytest

from src.trading.account_risk import (
    AccountRiskGuard,
    AccountRiskLimits,
    AccountRiskStore,
    EntryRiskBlocked,
)


class RiskClient:
    def __init__(self):
        self.positions = [{"instId": "BTC-USDT-SWAP", "pos": "-1", "notionalUsd": "100"}]
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

    def get_positions(self, *, inst_type):
        return self.positions

    def get_pending_orders(self, *, inst_type):
        return self.pending


def _store(tmp_path, *, order="50", total="200", leverage="10", enabled=True):
    store = AccountRiskStore(str(tmp_path / "trades.db"))
    store.save(
        AccountRiskLimits(
            enabled=enabled,
            max_order_notional_usd=Decimal(order),
            max_total_exposure_usd=Decimal(total),
            max_leverage=Decimal(leverage),
        )
    )
    store.set_entries_enabled(True)
    return store


def test_account_risk_store_persists_one_versioned_envelope(tmp_path):
    store = _store(tmp_path)

    limits = AccountRiskStore(store.db_path).get()

    assert limits is not None
    assert limits.enabled is True
    assert limits.max_order_notional_usd == Decimal("50")
    assert limits.max_total_exposure_usd == Decimal("200")
    assert limits.max_leverage == Decimal("10")
    assert limits.entries_enabled is True
    assert store.applied_schema_versions() == [1, 2]


def test_entry_approval_counts_positions_pending_entries_and_requested_order(tmp_path):
    approval = AccountRiskGuard(RiskClient(), _store(tmp_path)).approve_entry(
        inst_id="ETH-USDT-SWAP",
        requested_size="2",
        entry_price="2000",
    )

    assert approval.order_notional_usd == Decimal("40.00")
    assert approval.existing_exposure_usd == Decimal("138.00")
    assert approval.projected_exposure_usd == Decimal("178.00")
    assert approval.leverage == Decimal("5")


@pytest.mark.parametrize(
    ("store_kwargs", "client_change", "expected"),
    [
        ({"order": "30"}, None, "order notional"),
        ({"total": "170"}, None, "projected exposure"),
        ({"leverage": "4"}, ("leverage", "5"), "configured leverage"),
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
            requested_size="2",
            entry_price="2000",
        )


def test_entry_approval_fails_closed_when_exchange_exposure_is_incomplete(tmp_path):
    client = RiskClient()
    client.positions = [{"instId": "BTC-USDT-SWAP", "pos": "1", "notionalUsd": ""}]

    with pytest.raises(EntryRiskBlocked, match="has no notionalUsd"):
        AccountRiskGuard(client, _store(tmp_path)).approve_entry(
            inst_id="ETH-USDT-SWAP",
            requested_size="1",
            entry_price="2000",
        )


def test_entry_approval_is_disabled_by_default_and_survives_restart(tmp_path):
    store = AccountRiskStore(str(tmp_path / "trades.db"))
    store.save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=Decimal("50"),
            max_total_exposure_usd=Decimal("200"),
            max_leverage=Decimal("10"),
        )
    )

    with pytest.raises(EntryRiskBlocked, match="disabled by the operator"):
        AccountRiskGuard(RiskClient(), AccountRiskStore(store.db_path)).approve_entry(
            inst_id="ETH-USDT-SWAP",
            requested_size="1",
            entry_price="2000",
        )
