import pytest

from src.trading.logical_position_store import LogicalPositionStore
from src.trading.position_import import (
    PositionImportConflict,
    PositionImportRequest,
    PositionImportService,
)


class ImportClient:
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
    service = PositionImportService(ImportClient(), store)

    position = service.import_unexplained(_request())

    assert position.source == "import"
    assert position.opened_quantity == 2
    assert position.entry_price == 3000
    assert store.list_close_conditions(position.id)[0].expression["symbol"] == "ETH-USDT-SWAP"
    report = service.reconciler.reconcile_account(
        logical_positions=store.list_active(),
        exchange_positions=ImportClient().get_positions(inst_type="SWAP"),
    )
    assert report.safe_for_entries is False
    assert report.state == "protection_required"
    assert report.unprotected_position_ids == [position.id]
    with pytest.raises(PositionImportConflict, match="no unexplained"):
        service.import_unexplained(_request())


def test_import_requires_side_consistent_stop_loss(tmp_path):
    store = LogicalPositionStore(str(tmp_path / "trades.db"))
    request = _request()
    request.close_conditions[0]["expression"]["type"] = "price_above"

    with pytest.raises(ValueError, match="side-consistent"):
        PositionImportService(ImportClient(), store).import_unexplained(request)

    assert store.list_active() == []
