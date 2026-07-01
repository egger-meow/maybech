from decimal import Decimal

import pytest

from src.exchange.client import (
    arm_order_placement,
    disarm_order_placement,
    enable_entry_order_placement,
)
from src.trading.account_risk import AccountRiskLimits, AccountRiskStore
from src.trading.audit_event_store import AuditEventStore
from src.trading.entry_control import EntryControlManager
from src.trading.logical_position_store import LogicalPositionRecord, LogicalPositionStore


class ControlClient:
    def __init__(self):
        self.cancel_calls = []
        self.orders = {
            "client-only": {"ordId": "recovered-order", "state": "live"},
        }
        self.cancel_errors = set()

    def get_order(self, inst_id, order_id="", client_order_id=""):
        order = self.orders.get(order_id or client_order_id)
        return [] if order is None else [order]

    def cancel_order(self, inst_id, order_id):
        self.cancel_calls.append((inst_id, order_id))
        if order_id in self.cancel_errors:
            raise RuntimeError("cancel unavailable")
        return {"ordId": order_id, "sCode": "0"}


def _manager(tmp_path, client=None):
    db_path = str(tmp_path / "trades.db")
    risk_store = AccountRiskStore(db_path)
    risk_store.save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=Decimal("100"),
            max_total_exposure_usd=Decimal("1000"),
            max_leverage=Decimal("5"),
            allowed_instruments=("BTC-USDT-SWAP", "ETH-USDT-SWAP"),
        )
    )
    risk_store.set_entries_enabled(True)
    position_store = LogicalPositionStore(db_path)
    return EntryControlManager(
        client=client or ControlClient(),
        risk_store=risk_store,
        position_store=position_store,
        audit_store=AuditEventStore(db_path),
    )


def test_entry_kill_persists_first_and_cancels_only_pending_entries(tmp_path):
    client = ControlClient()
    manager = _manager(tmp_path, client)
    manager.position_store.save(
        LogicalPositionRecord(
            id="entry-with-order",
            inst_id="ETH-USDT-SWAP",
            status="pending_open",
            exchange_order_id="entry-order",
        )
    )
    manager.position_store.save(
        LogicalPositionRecord(
            id="entry-client-only",
            inst_id="BTC-USDT-SWAP",
            status="pending_open",
            client_order_id="client-only",
        )
    )
    manager.position_store.save(
        LogicalPositionRecord(
            id="closing-unit",
            inst_id="ETH-USDT-SWAP",
            status="closing",
            exchange_order_id="close-order",
        )
    )
    arm_order_placement(preflight_passed=True)
    enable_entry_order_placement()

    result = manager.kill_entries()

    assert result.entries_enabled is False
    assert result.process_entry_enabled is False
    assert result.persisted is True
    assert result.pending_entries == 2
    assert result.cancellations_requested == 2
    assert result.unresolved == 0
    assert client.cancel_calls == [
        ("ETH-USDT-SWAP", "entry-order"),
        ("BTC-USDT-SWAP", "recovered-order"),
    ]
    assert manager.risk_store.entries_enabled() is False
    assert manager.position_store.is_order_cancel_requested(
        "entry-with-order", exchange_order_id="entry-order"
    )
    assert manager.position_store.get("closing-unit").exchange_order_id == "close-order"
    audits = manager.audit_store.list(event_type="entry_control.killed")
    assert len(audits) == 1
    disarm_order_placement()


def test_entry_kill_stays_disabled_when_some_cancellations_fail(tmp_path):
    client = ControlClient()
    client.cancel_errors.add("entry-order")
    manager = _manager(tmp_path, client)
    manager.position_store.save(
        LogicalPositionRecord(
            id="entry-unit",
            inst_id="ETH-USDT-SWAP",
            status="pending_open",
            exchange_order_id="entry-order",
        )
    )

    result = manager.kill_entries()

    assert result.entries_enabled is False
    assert result.unresolved == 1
    assert "cancel unavailable" in result.errors[0]
    assert manager.risk_store.entries_enabled() is False


def test_entry_enable_requires_an_armed_live_process(tmp_path):
    manager = _manager(tmp_path)
    manager.risk_store.set_entries_enabled(False)
    disarm_order_placement()

    with pytest.raises(PermissionError, match="armed live runtime"):
        manager.enable_entries()
    assert manager.risk_store.entries_enabled() is False

    arm_order_placement(preflight_passed=True)
    online = manager.enable_entries()
    assert online.entries_enabled is True
    assert online.process_entry_enabled is True
    disarm_order_placement()


def test_live_startup_reset_disables_persisted_and_process_entry_gates(tmp_path):
    manager = _manager(tmp_path)
    arm_order_placement(preflight_passed=True)
    enable_entry_order_placement()

    result = manager.disable_for_startup()

    assert result.entries_enabled is False
    assert result.process_entry_enabled is False
    assert manager.risk_store.entries_enabled() is False
    audits = manager.audit_store.list(event_type="entry_control.startup_disabled")
    assert len(audits) == 1


def test_entry_enable_rolls_back_when_audit_cannot_be_persisted(tmp_path):
    manager = _manager(tmp_path)

    class FailingAuditStore:
        def create(self, **kwargs):
            raise OSError("audit unavailable")

    manager.audit_store = FailingAuditStore()
    arm_order_placement(preflight_passed=True)

    with pytest.raises(OSError, match="audit unavailable"):
        manager.enable_entries()

    assert manager.risk_store.entries_enabled() is False
    assert manager.status().process_entry_enabled is False
    disarm_order_placement()
