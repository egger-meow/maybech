import pytest

from scripts.verify_okx_live_safe import prove_mutations_disarmed
from src.exchange.client import arm_order_placement, disarm_order_placement


def test_live_safe_verifier_blocks_every_mutation_before_transport():
    arm_order_placement(preflight_passed=True)
    try:
        assert prove_mutations_disarmed() == [
            "order",
            "cancel",
            "reduce",
            "close",
            "algo_place",
            "algo_amend",
            "algo_cancel",
        ]
    finally:
        disarm_order_placement()


def test_live_safe_verifier_requires_explicit_production_environment(monkeypatch):
    from scripts import verify_okx_live_safe as verifier

    monkeypatch.setenv("OKX_FLAG", "1")

    with pytest.raises(RuntimeError, match="production OKX_FLAG=0"):
        verifier.run("unused.db")
