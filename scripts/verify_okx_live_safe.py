"""Prove production account inspection and recovery with mutations disarmed."""

# ruff: noqa: E402 -- direct script execution needs the repository root on sys.path.

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import settings
from src.exchange.client import OKXClient, disarm_order_placement
from src.runtime.live_preflight import run_live_preflight
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.position_import import PositionRecoveryService
from src.trading.position_reconciliation import PositionReconciler
from src.trading.strategy_store import StrategyStore


class _MutationTrap:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def reached(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            self.calls.append(name)
            raise AssertionError(f"mutation transport reached: {name}")

        return reached


def prove_mutations_disarmed() -> list[str]:
    """Exercise every exchange mutation guard without a network transport."""
    disarm_order_placement()
    trap = _MutationTrap()
    client = object.__new__(OKXClient)
    client.trade_api = trap
    attempts = {
        "order": lambda: client.place_limit_order(
            "BTC-USDT-SWAP", "buy", "0.01", "1", client_order_id="livesafe1"
        ),
        "cancel": lambda: client.cancel_order("BTC-USDT-SWAP", "order1"),
        "reduce": lambda: client.place_reduce_market_order(
            inst_id="BTC-USDT-SWAP",
            position_side="long",
            sz="0.01",
            client_order_id="livesafe2",
            confirm=True,
        ),
        "close": lambda: client.place_reduce_market_order(
            inst_id="BTC-USDT-SWAP",
            position_side="long",
            sz="0.01",
            client_order_id="livesafe3",
            confirm=True,
        ),
        "algo_place": lambda: client.place_position_stop(
            inst_id="BTC-USDT-SWAP",
            position_side="long",
            sz="0.01",
            stop_trigger_px="1",
            algo_client_order_id="livesafe4",
            confirm=True,
        ),
        "algo_amend": lambda: client.amend_position_stop(
            inst_id="BTC-USDT-SWAP",
            algo_id="algo1",
            sz="0.01",
            stop_trigger_px="1",
            confirm=True,
        ),
        "algo_cancel": lambda: client.cancel_position_stop(
            inst_id="BTC-USDT-SWAP", algo_id="algo1", confirm=True
        ),
    }
    blocked: list[str] = []
    for name, attempt in attempts.items():
        try:
            attempt()
        except PermissionError:
            blocked.append(name)
        else:
            raise RuntimeError(f"Live Safe mutation was not blocked: {name}")
    if trap.calls:
        raise RuntimeError(f"Live Safe reached mutation transport: {trap.calls}")
    return blocked


def run(db_path: str) -> dict[str, Any]:
    if os.getenv("OKX_FLAG") != "0" or settings.OKX_FLAG != "0":
        raise RuntimeError("Live Safe verifier requires production OKX_FLAG=0")
    if os.getenv("MAYBECH_ARM_ORDERS", "0") == "1":
        raise RuntimeError("Live Safe verifier requires MAYBECH_ARM_ORDERS=0")

    blocked = prove_mutations_disarmed()
    client = OKXClient()
    if client.flag != "0":
        raise RuntimeError("Live Safe verifier selected non-production credentials")

    store = LogicalPositionStore(db_path)
    preflight = run_live_preflight(
        client=client,
        strategy_store=StrategyStore(db_path),
        position_store=store,
        include_strategy=False,
        runtime_mode="live_safe",
    )
    balances = client.get_balance()
    account_configs = client.get_account_config()
    positions = client.get_positions(inst_type="SWAP")
    pending_orders = client.get_pending_orders(inst_type="SWAP")
    order_history = client.get_order_history(inst_type="SWAP", limit="20")
    pending_algos = client.get_pending_algo_orders(ord_type="conditional")
    fills = client.get_fills_history(inst_type="SWAP", limit="20")

    recovered = PositionRecoveryService(store).reconcile(positions)
    reconciliation = PositionReconciler().reconcile_account(
        logical_positions=store.list_active(),
        exchange_positions=positions,
    )
    if len(account_configs) != 1 or not balances:
        raise RuntimeError("production account reads returned incomplete state")
    if preflight.execution_mode != "live_safe" or preflight.order_submission_enabled:
        raise RuntimeError("Live Safe preflight reported an unsafe execution mode")

    return {
        "mode": preflight.execution_mode,
        "credential_environment": preflight.credential_environment,
        "order_submission_enabled": preflight.order_submission_enabled,
        "mutation_methods_blocked": blocked,
        "position_count": len(positions),
        "pending_order_count": len(pending_orders),
        "order_history_count": len(order_history),
        "pending_algo_count": len(pending_algos),
        "fill_history_count": len(fills),
        "recovered_position_count": len(recovered),
        "reconciliation_state": reconciliation.state,
        "reconciliation_group_count": len(reconciliation.groups),
        "audit_db": db_path,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-production-read-only", action="store_true")
    parser.add_argument("--db-path", required=True)
    args = parser.parse_args()
    if not args.confirm_production_read_only:
        parser.error("--confirm-production-read-only is required")
    print(json.dumps(run(args.db_path), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
