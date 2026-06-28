import json

import pytest

from src.trading.logical_position_store import LogicalPositionStore
from src.trading.position_import import (
    PositionImportConflict,
    PositionImportRequest,
    PositionImportService,
)


class ImportClient:
    def __init__(self):
        self.pending = []
        self.placements = []
        self.amendments = []

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
        order["sz"] = kwargs["sz"]
        order["slTriggerPx"] = kwargs["stop_trigger_px"]
        return {"algoId": order["algoId"], "sCode": "0"}


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
