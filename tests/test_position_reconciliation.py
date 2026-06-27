from src.trading.logical_position_store import LogicalPositionRecord
from src.trading.position_reconciliation import PositionReconciler


def test_reconciler_balances_merged_okx_position_against_multiple_units():
    positions = [
        LogicalPositionRecord(
            id="unit-a",
            inst_id="ETH-USDT-SWAP",
            side="short",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=3000,
        ),
        LogicalPositionRecord(
            id="unit-b",
            inst_id="ETH-USDT-SWAP",
            side="short",
            opened_quantity=0.2,
            remaining_quantity=0.2,
            entry_price=3010,
        ),
    ]

    result = PositionReconciler().reconcile(
        logical_positions=positions,
        exchange_positions=[{"inst_id": "ETH-USDT-SWAP", "pos_side": "short", "position": "0.3"}],
    )

    assert result["unit-a"].state == "balanced"
    assert result["unit-b"].state == "balanced"
    assert result["unit-a"].group_logical_remaining == 0.3
    assert result["unit-a"].exchange_position_key == "ETH-USDT-SWAP:short"


def test_reconciler_flags_under_allocated_exchange_position():
    positions = [
        LogicalPositionRecord(
            id="unit-a",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=3000,
        )
    ]

    result = PositionReconciler().reconcile(
        logical_positions=positions,
        exchange_positions=[{"inst_id": "ETH-USDT-SWAP", "pos_side": "long", "position": "0.2"}],
    )

    assert result["unit-a"].state == "under_allocated"
    assert result["unit-a"].group_quantity_gap == 0.1


def test_reconciler_flags_over_allocated_logical_units():
    positions = [
        LogicalPositionRecord(
            id="unit-a",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.2,
            remaining_quantity=0.2,
            entry_price=3000,
        )
    ]

    result = PositionReconciler().reconcile(
        logical_positions=positions,
        exchange_positions=[{"inst_id": "ETH-USDT-SWAP", "pos_side": "long", "position": "0.1"}],
    )

    assert result["unit-a"].state == "over_allocated"
    assert result["unit-a"].group_quantity_gap == -0.1


def test_reconciler_reports_unknown_quantity_without_silent_allocation():
    positions = [
        LogicalPositionRecord(id="unit-a", inst_id="ETH-USDT-SWAP", side="long", entry_price=3000)
    ]

    result = PositionReconciler().reconcile(
        logical_positions=positions,
        exchange_positions=[{"inst_id": "ETH-USDT-SWAP", "pos_side": "long", "position": "0.1"}],
    )

    assert result["unit-a"].state == "unknown_quantity"
    assert result["unit-a"].group_logical_remaining is None


def test_reconciler_reports_missing_exchange_position():
    positions = [
        LogicalPositionRecord(
            id="unit-a",
            inst_id="ETH-USDT-SWAP",
            side="long",
            opened_quantity=0.1,
            remaining_quantity=0.1,
            entry_price=3000,
        )
    ]

    result = PositionReconciler().reconcile(logical_positions=positions, exchange_positions=[])

    assert result["unit-a"].state == "no_exchange_position"
    assert result["unit-a"].exchange_position_key == ""
