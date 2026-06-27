from decimal import Decimal

import pytest

from src.trading.instrument_constraints import InstrumentConstraints


def _constraints(**overrides) -> InstrumentConstraints:
    payload = {
        "instId": "ETH-USDT-SWAP",
        "state": "live",
        "minSz": "0.1",
        "lotSz": "0.1",
        "tickSz": "0.01",
        "ctVal": "0.01",
        **overrides,
    }
    return InstrumentConstraints.from_okx(payload)


def test_instrument_constraints_use_decimal_precision():
    constraints = _constraints()

    assert constraints.minimum_size == Decimal("0.1")
    assert constraints.normalize_size("0.3") == "0.3"
    assert constraints.normalize_price("2000.126") == "2000.13"


def test_instrument_constraints_reject_invalid_order_sizes():
    constraints = _constraints(minSz="1", lotSz="0.5")

    with pytest.raises(ValueError, match="below"):
        constraints.normalize_size("0.5")
    with pytest.raises(ValueError, match="multiple"):
        constraints.normalize_size("1.2")


def test_instrument_constraints_reject_non_live_instrument():
    with pytest.raises(ValueError, match="not tradable"):
        _constraints(state="suspend").normalize_size("1")
