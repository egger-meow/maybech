import json
from decimal import Decimal

import pytest

from src.exchange.client import (
    arm_order_placement,
    disarm_order_placement,
    enable_entry_order_placement,
    entry_order_placement_enabled,
)
from src.trading.account_risk import AccountRiskStore
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.position_import import (
    PositionImportConflict,
    PositionImportRequest,
    PositionImportService,
)
from src.trading.position_protection import PositionProtectionError


class ImportClient:
    def __init__(self):
        self.pending = []
        self.placements = []
        self.amendments = []
        self.amend_error = None
        self.apply_amend = True
        self.drop_on_amend = False
        self.cancellations = []
        self.cancel_error = None
        self.ticker = "3100"

    def get_positions(self, *, inst_type):
        assert inst_type == "SWAP"
        return [
            {
                "instId": "ETH-USDT-SWAP",
                "posSide": "net",
                "pos": "2",
                "avgPx": "3000",
                "markPx": "3100",
            }
        ]

    def get_instruments(self, *, inst_type, inst_id):
        assert inst_type == "SWAP"
        return [
            {
                "instId": inst_id,
                "state": "live",
                "minSz": "1",
                "lotSz": "1",
                "tickSz": "0.1",
            }
        ]

    def get_ticker(self, *, inst_id):
        return [{"instId": inst_id, "last": self.ticker}]

    def get_pending_algo_orders(self, *, inst_id):
        return [item for item in self.pending if item["instId"] == inst_id]

    def place_position_stop(self, **kwargs):
        self.placements.append(kwargs)
        order = {
            "algoId": "protect-1",
            "algoClOrdId": kwargs["algo_client_order_id"],
            "instId": kwargs["inst_id"],
            "side": "sell" if kwargs["position_side"] == "long" else "buy",
            "ordType": "conditional",
            "state": "live",
            "posSide": "net",
            "reduceOnly": "true",
            "sz": kwargs["sz"],
            "slTriggerPx": kwargs["stop_trigger_px"],
            "slOrdPx": "-1",
        }
        self.pending.append(order)
        return {"algoId": order["algoId"], "sCode": "0"}

    def amend_position_stop(self, **kwargs):
        self.amendments.append(kwargs)
        order = self.pending[0]
        if self.apply_amend:
            order["sz"] = kwargs["sz"]
            order["slTriggerPx"] = kwargs["stop_trigger_px"]
        if self.drop_on_amend:
            self.pending = []
        if self.amend_error is not None:
            raise self.amend_error
        return {"algoId": order["algoId"], "sCode": "0"}

    def cancel_position_stop(self, **kwargs):
        self.cancellations.append(kwargs)
        if self.cancel_error is not None:
            raise self.cancel_error
        self.pending = [
            item for item in self.pending if item["algoId"] != kwargs["algo_id"]
        ]
        return {"algoId": kwargs["algo_id"], "sCode": "0"}


def _request():
    return PositionImportRequest(
        inst_id="ETH-USDT-SWAP",
        side="long",
        close_conditions=[
            {
                "purpose": "stop_loss",
                "expression": {"type": "price_below", "symbol": "self", "value": 2900},
                "enabled": True,
            }
        ],
        reason="adopt position opened outside Maybech",
    )


def test_import_creates_exact_gap_with_required_stop_and_is_not_repeatable(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    client = ImportClient()
    service = PositionImportService(client, store)

    position = service.import_unexplained(_request())

    assert position.source == "import"
    assert position.opened_quantity == 2
    assert position.entry_price == 3000
    metadata = json.loads(position.metadata_json)
    assert metadata["exchange_protection_verified"] is True
    assert metadata["exchange_protection"]["algo_id"] == "protect-1"
    assert store.get_protection(position.id).status == "active"
    assert client.placements[0]["sz"] == "2"
    assert client.placements[0]["stop_trigger_px"] == "2900"
    assert store.list_close_conditions(position.id)[0].expression["symbol"] == "ETH-USDT-SWAP"
    report = service.reconciler.reconcile_account(
        logical_positions=store.list_active(),
        exchange_positions=client.get_positions(inst_type="SWAP"),
    )
    assert report.safe_for_entries is True
    assert report.state == "balanced"
    assert report.unprotected_position_ids == []
    with pytest.raises(PositionImportConflict, match="no unexplained"):
        service.import_unexplained(_request())


def test_import_requires_side_consistent_stop_loss(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    request = _request()
    request.close_conditions[0]["expression"]["type"] = "price_above"

    with pytest.raises(ValueError, match="side-consistent"):
        PositionImportService(ImportClient(), store).import_unexplained(request)

    assert store.list_active() == []


def test_protection_retry_amends_existing_stop_after_condition_change(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    client = ImportClient()
    service = PositionImportService(client, store)
    position = service.import_unexplained(_request())
    condition = store.list_close_conditions(position.id)[0]
    store.update_close_condition(
        position.id,
        condition.id,
        expression={"type": "price_below", "symbol": position.inst_id, "value": 2850},
    )
    store.merge_metadata(position.id, {"exchange_protection_verified": False})

    updated = service.protection.protect(position.id)

    assert client.amendments[0]["stop_trigger_px"] == "2850"
    assert json.loads(updated.metadata_json)["exchange_protection"]["stop_loss"] == "2850"


def test_confirmed_stop_amend_updates_exchange_rule_and_owned_protection(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    client = ImportClient()
    service = PositionImportService(client, store)
    position = service.import_unexplained(_request())
    condition = store.list_close_conditions(position.id)[0]

    updated = service.protection.amend_stop_condition(
        position.id,
        condition.id,
        expression={"type": "price_below", "symbol": "self", "value": 2850},
        reason="tighten operator stop",
    )

    assert client.amendments == [
        {
            "inst_id": "ETH-USDT-SWAP",
            "algo_id": "protect-1",
            "sz": "2",
            "stop_trigger_px": "2850",
            "confirm": True,
        }
    ]
    assert store.get_close_condition(position.id, condition.id).expression["value"] == 2850
    protection = store.get_protection(position.id)
    assert protection.status == "active"
    assert protection.stop_loss == 2850
    assert protection.metadata["stop_amend"]["status"] == "completed"
    assert json.loads(updated.metadata_json)["exchange_protection"]["stop_loss"] == "2850"
    assert len(
        service.protection.audit_store.list(
            event_type="position.protection_stop_amended",
            position_id=position.id,
        )
    ) == 1


def test_failed_stop_amend_keeps_original_rule_when_exchange_proves_old_stop(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    client = ImportClient()
    service = PositionImportService(client, store)
    position = service.import_unexplained(_request())
    condition = store.list_close_conditions(position.id)[0]
    client.apply_amend = False
    client.amend_error = TimeoutError("response lost")

    with pytest.raises(PositionProtectionError, match="was not applied"):
        service.protection.amend_stop_condition(
            position.id,
            condition.id,
            expression={"type": "price_below", "symbol": "self", "value": 2850},
            reason="tighten operator stop",
        )

    assert store.get_close_condition(position.id, condition.id).expression["value"] == 2900
    protection = store.get_protection(position.id)
    assert protection.status == "active"
    assert protection.stop_loss == 2900
    assert protection.metadata["stop_amend"]["status"] == "not_applied"


def test_unknown_stop_amend_outcome_fails_protection_and_disables_entries(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    client = ImportClient()
    service = PositionImportService(client, store)
    position = service.import_unexplained(_request())
    condition = store.list_close_conditions(position.id)[0]
    service.protection.store.update_protection(position.id, status="active")
    risk_store = AccountRiskStore(store.db_path)
    risk_store.set_entries_enabled(True)
    arm_order_placement(preflight_passed=True)
    enable_entry_order_placement()
    client.amend_error = TimeoutError("response lost")
    client.drop_on_amend = True

    try:
        with pytest.raises(PositionProtectionError, match="outcome unknown"):
            service.protection.amend_stop_condition(
                position.id,
                condition.id,
                expression={"type": "price_below", "symbol": "self", "value": 2850},
                reason="tighten operator stop",
            )

        assert store.get_close_condition(position.id, condition.id).expression["value"] == 2900
        assert store.get_protection(position.id).status == "failed"
        assert risk_store.entries_enabled() is False
        assert entry_order_placement_enabled() is False
    finally:
        disarm_order_placement()


def test_stop_amend_rejects_stale_protection_quantity_before_submission(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    client = ImportClient()
    service = PositionImportService(client, store)
    position = service.import_unexplained(_request())
    condition = store.list_close_conditions(position.id)[0]
    store.update_protection(position.id, quantity=1)

    with pytest.raises(PositionProtectionError, match="quantity does not match"):
        service.protection.amend_stop_condition(
            position.id,
            condition.id,
            expression={"type": "price_below", "symbol": "self", "value": 2850},
            reason="tighten operator stop",
        )

    assert client.amendments == []


def test_break_even_reuses_confirmed_amend_and_persists_operation_evidence(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    client = ImportClient()
    service = PositionImportService(client, store)
    position = service.import_unexplained(_request())
    condition = store.list_close_conditions(position.id)[0]

    updated = service.protection.move_to_break_even(
        position.id,
        condition.id,
        lock_in_pct=Decimal("0.01"),
        reason="protect one percent profit",
    )

    assert client.amendments[0]["stop_trigger_px"] == "3030"
    protection = store.get_protection(position.id)
    assert protection.stop_loss == 3030
    assert protection.metadata["stop_amend"]["operation"] == "break_even"
    saved_condition = store.get_close_condition(position.id, condition.id)
    assert saved_condition.expression["value"] == 3030
    assert saved_condition.metadata["break_even"]["status"] == "applied"
    assert json.loads(updated.metadata_json)["exchange_protection"]["stop_loss"] == "3030"


def test_break_even_rejects_unfavorable_current_price_without_amending(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    client = ImportClient()
    service = PositionImportService(client, store)
    position = service.import_unexplained(_request())
    condition = store.list_close_conditions(position.id)[0]
    client.ticker = "2999"

    with pytest.raises(PositionProtectionError, match="has not moved beyond"):
        service.protection.move_to_break_even(
            position.id,
            condition.id,
            lock_in_pct=Decimal("0"),
            reason="premature break-even",
        )

    assert client.amendments == []
    assert store.get_protection(position.id).stop_loss == 2900


def test_protection_can_be_canceled_and_proven_absent_before_close(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    client = ImportClient()
    service = PositionImportService(client, store)
    position = service.import_unexplained(_request())

    canceled = service.protection.cancel_for_close(
        position.id,
        reason="operator_requested:test",
    )

    protection = store.get_protection(position.id)
    assert canceled is True
    assert protection.status == "canceled"
    assert client.pending == []
    assert client.cancellations == [
        {"inst_id": "ETH-USDT-SWAP", "algo_id": "protect-1", "confirm": True}
    ]


def test_unknown_cancel_outcome_is_persisted_as_failed(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    client = ImportClient()
    service = PositionImportService(client, store)
    position = service.import_unexplained(_request())
    client.cancel_error = TimeoutError("response lost")

    with pytest.raises(PositionProtectionError, match="outcome is unknown"):
        service.protection.cancel_for_close(position.id, reason="test close")

    protection = store.get_protection(position.id)
    assert protection.status == "failed"
    assert protection.metadata["cancel_outcome"] == "unknown"
