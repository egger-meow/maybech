"""Run one bounded, minimum-size OKX staged execution lifecycle.

This command is intentionally not a pytest test. It requires an environment-
specific CLI confirmation plus matching OKX_FLAG and MAYBECH_ARM_ORDERS=1.
"""

# ruff: noqa: E402 -- direct script execution needs the repository root on sys.path.

from __future__ import annotations

import argparse
import json
import sys
import time
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config.settings import settings
from src.daemon.execution_fill_service import ExecutionFillService
from src.daemon.position_manager_service import PositionManagerService
from src.daemon.service import DaemonRunner
from src.exchange.client import (
    OKXClient,
    arm_order_placement,
    disable_entry_order_placement,
    disarm_order_placement,
    enable_entry_order_placement,
)
from src.exchange.fills import normalize_okx_fill
from src.runtime.live_preflight import run_live_preflight
from src.trading.account_risk import AccountRiskLimits, AccountRiskStore
from src.trading.audit_event_store import AuditEventStore
from src.trading.execution_allocation import (
    ConfirmedExecutionFill,
    ExecutionAllocationService,
)
from src.trading.execution_cursor_store import ExecutionCursorStore
from src.trading.executor import Executor
from src.trading.instrument_constraints import InstrumentConstraints
from src.trading.logical_position_store import (
    LogicalPositionProtection,
    LogicalPositionRecord,
    LogicalPositionStore,
)
from src.trading.position_protection import PositionProtectionService
from src.trading.strategy_store import StrategyStore
from src.trading.trade_store import TradeRecord, TradeStore


INSTRUMENT = "BTC-USDT-SWAP"
OPEN_SIZE = Decimal("0.02")
REDUCE_SIZE = Decimal("0.01")


def _wait_for_order(client: OKXClient, order_id: str, *, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        orders = client.get_order(INSTRUMENT, order_id=order_id)
        if len(orders) == 1:
            order = orders[0]
            if str(order.get("state") or "").lower() in {
                "filled",
                "canceled",
                "mmp_canceled",
                "rejected",
            }:
                return order
        time.sleep(0.25)
    raise TimeoutError(f"order {order_id} did not become terminal")


def _wait_for_fills(client: OKXClient, order_id: str, *, timeout: float = 3) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        fills = client.get_fills(
            inst_type="SWAP",
            inst_id=INSTRUMENT,
            order_id=order_id,
            limit="100",
        )
        if not fills:
            fills = [
                fill
                for fill in client.get_fills_history(inst_type="SWAP", limit="100")
                if str(fill.get("ordId") or "") == order_id
            ]
        if fills:
            return fills
        time.sleep(0.25)
    raise TimeoutError(f"fills for order {order_id} did not arrive")


def _pending_protection(client: OKXClient, algo_client_id: str) -> dict:
    matches = []
    for order_type in ("conditional", "oco"):
        matches.extend(
            order
            for order in client.get_pending_algo_orders(
                inst_id=INSTRUMENT,
                ord_type=order_type,
            )
            if str(order.get("algoClOrdId") or "") == algo_client_id
        )
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one active protection {algo_client_id}, got {len(matches)}"
        )
    return matches[0]


def _ingest_order_fills(
    client: OKXClient,
    allocator: ExecutionAllocationService,
    order_id: str,
) -> list[str]:
    statuses = []
    try:
        fills = _wait_for_fills(client, order_id)
    except TimeoutError:
        order = _wait_for_order(client, order_id)
        if str(order.get("state") or "").lower() != "filled":
            raise
        quantity = Decimal(str(order.get("accFillSz") or "0"))
        price = Decimal(str(order.get("avgPx") or order.get("fillPx") or "0"))
        if quantity <= 0 or price <= 0:
            raise RuntimeError("filled order lacks recoverable quantity or price")
        result = allocator.ingest(
            ConfirmedExecutionFill(
                fill_id=f"order-recovery:{order_id}",
                exchange_order_id=order_id,
                client_order_id=str(order.get("clOrdId") or ""),
                quantity=float(quantity),
                price=float(price),
                confirmation_source="recovery",
                reason="authenticated OKX terminal-order recovery",
                metadata={"order_state": "filled", "source": "get_order"},
            )
        )
        return [result.execution_status]
    for payload in fills:
        result = allocator.ingest(normalize_okx_fill(payload))
        statuses.append(result.execution_status)
    return statuses


def _audit(
    audit_store: AuditEventStore,
    run_id: str,
    environment: str,
    stage: str,
    payload: dict,
) -> None:
    audit_store.create(
        type=f"verification.{environment}_{stage}",
        source="verify_okx_demo_lifecycle",
        payload={
            "run_id": run_id,
            "environment": environment,
            "instrument": INSTRUMENT,
            **payload,
        },
    )


def _cleanup(
    client: OKXClient,
    *,
    owned_order_ids: set[str],
    owned_algo_ids: set[str],
    cleanup_prefix: str,
) -> list[str]:
    actions: list[str] = []
    try:
        for order in client.get_pending_orders(inst_type="SWAP"):
            order_id = str(order.get("ordId") or "")
            if order_id in owned_order_ids:
                client.cancel_order(INSTRUMENT, order_id)
                actions.append(f"canceled_order:{order_id}")
    except Exception as exc:  # Cleanup must continue to exposure removal.
        actions.append(f"order_cleanup_error:{exc}")
    try:
        for order_type in ("conditional", "oco"):
            for order in client.get_pending_algo_orders(
                inst_id=INSTRUMENT,
                ord_type=order_type,
            ):
                algo_id = str(order.get("algoId") or "")
                if algo_id in owned_algo_ids:
                    client.cancel_position_stop(
                        inst_id=INSTRUMENT,
                        algo_id=algo_id,
                        confirm=True,
                    )
                    actions.append(f"canceled_algo:{algo_id}")
    except Exception as exc:
        actions.append(f"algo_cleanup_error:{exc}")
    try:
        for index, position in enumerate(client.get_positions(inst_id=INSTRUMENT)):
            quantity = Decimal(str(position.get("pos") or "0"))
            if quantity == 0:
                continue
            client.place_reduce_market_order(
                inst_id=INSTRUMENT,
                position_side="long" if quantity > 0 else "short",
                sz=str(abs(quantity)),
                pos_side="net",
                client_order_id=f"{cleanup_prefix}{index}",
                confirm=True,
            )
            actions.append(f"closed_residual:{abs(quantity)}")
    except Exception as exc:
        actions.append(f"exposure_cleanup_error:{exc}")
    return actions


def run(db_path: str, *, environment: str = "demo") -> dict:
    if environment not in {"demo", "production"}:
        raise ValueError("environment must be demo or production")
    expected_flag = "1" if environment == "demo" else "0"
    if settings.OKX_FLAG != expected_flag:
        raise RuntimeError(
            f"{environment} lifecycle verifier requires OKX_FLAG={expected_flag}"
        )
    client = OKXClient()
    if client.flag != expected_flag:
        raise RuntimeError(f"OKX client is not connected to {environment}")
    if any(
        Decimal(str(position.get("pos") or "0")) != 0
        for position in client.get_positions(inst_id=INSTRUMENT)
    ):
        raise RuntimeError(f"refusing to start with existing {INSTRUMENT} exposure")

    run_id = uuid4().hex[:12]
    prefix = f"mb{'d' if environment == 'demo' else 'p'}{run_id}"
    owned_order_ids: set[str] = set()
    owned_algo_ids: set[str] = set()
    trade_store = TradeStore(db_path)
    position_store = LogicalPositionStore(db_path)
    audit_store = AuditEventStore(db_path)
    risk_store = AccountRiskStore(db_path)
    risk_store.save(
        AccountRiskLimits(
            enabled=True,
            max_order_notional_usd=Decimal("25"),
            max_total_exposure_usd=Decimal("25"),
            max_leverage=Decimal("3"),
            allowed_instruments=(INSTRUMENT,),
        )
    )
    risk_store.set_entries_enabled(True)
    report = run_live_preflight(
        client=client,
        strategy_store=StrategyStore(db_path),
        position_store=position_store,
        include_strategy=False,
    )
    arm_order_placement(preflight_passed=report.passed)
    enable_entry_order_placement()
    executor = Executor(client, dry_run=False, risk_store=risk_store)
    protection_service = PositionProtectionService(client, position_store, audit_store)
    allocator = ExecutionAllocationService(trade_store, position_store, audit_store)
    cleanup_actions: list[str] = []

    try:
        instrument = client.get_instruments(inst_type="SWAP", inst_id=INSTRUMENT)[0]
        constraints = InstrumentConstraints.from_okx(instrument)
        constraints.validate_tradable()
        if Decimal(str(instrument.get("minSz"))) > REDUCE_SIZE:
            raise RuntimeError("configured verifier size is below current OKX minimum")
        ticker = client.get_ticker(INSTRUMENT)[0]
        ask = Decimal(str(ticker.get("askPx") or ticker.get("last")))

        cancel_client_id = f"{prefix}c"
        far_price = constraints.normalize_entry_limit(
            ask * Decimal("0.5"),
            position_side="long",
        )
        cancel_result = client.place_limit_order(
            inst_id=INSTRUMENT,
            side="buy",
            sz=str(REDUCE_SIZE),
            px=far_price,
            client_order_id=cancel_client_id,
            order_type="limit",
            confirm=True,
        )
        cancel_order_id = str(cancel_result["ordId"])
        owned_order_ids.add(cancel_order_id)
        client.cancel_order(INSTRUMENT, cancel_order_id)
        canceled = _wait_for_order(client, cancel_order_id)
        if str(canceled.get("state") or "").lower() != "canceled":
            raise RuntimeError(
                f"{environment} pending order did not confirm canceled"
            )
        _audit(
            audit_store,
            run_id,
            environment,
            "cancel_confirmed",
            {"order_id": cancel_order_id},
        )

        entry_client_id = f"{prefix}o"
        position_id = f"{environment}-{run_id}"
        limit_price = constraints.normalize_entry_limit(
            ask * Decimal("1.002"),
            position_side="long",
        )
        stop_price = constraints.normalize_price(ask * Decimal("0.95"))
        amended_stop = constraints.normalize_price(ask * Decimal("0.94"))
        approval = executor.approve_entry(
            inst_id=INSTRUMENT,
            requested_size=str(OPEN_SIZE),
            entry_price=limit_price,
        )
        trade_store.save_trade(
            TradeRecord(
                id=position_id,
                inst_id=INSTRUMENT,
                side="long",
                entry_price=float(limit_price),
                status="pending_open",
                signal_reason=f"bounded {environment} lifecycle verification",
            )
        )
        position_store.save(
            LogicalPositionRecord(
                id=position_id,
                source="manual",
                trade_id=position_id,
                inst_id=INSTRUMENT,
                side="long",
                opened_quantity=0,
                remaining_quantity=0,
                entry_price=float(limit_price),
                status="pending_open",
                client_order_id=entry_client_id,
                metadata_json=json.dumps(
                    {
                        "correlation_id": run_id,
                        "order_action": "open",
                        "expected_quantity": float(OPEN_SIZE),
                    }
                ),
            )
        )
        condition = position_store.create_close_condition(
            position_id=position_id,
            purpose="stop_loss",
            expression={
                "type": "price_below",
                "symbol": INSTRUMENT,
                "value": float(stop_price),
            },
        )
        if condition is None:
            raise RuntimeError(f"failed to persist {environment} stop condition")
        entry_result = executor.execute(
            inst_id=INSTRUMENT,
            position_side="long",
            entry_price=float(limit_price),
            requested_size=str(OPEN_SIZE),
            stop_loss_price=float(stop_price),
            client_order_id=entry_client_id,
            risk_approval=approval,
        )
        entry_order_id = str(entry_result.get("ordId") or "")
        if not entry_order_id or not entry_result.get("maybechProtectionVerified"):
            raise RuntimeError(f"protected {environment} entry failed: {entry_result}")
        owned_order_ids.add(entry_order_id)
        position_store.link_exchange_order(
            position_id,
            client_order_id=entry_client_id,
            exchange_order_id=entry_order_id,
        )
        active_proof = entry_result["maybechProtection"]["active"]
        owned_algo_ids.add(str(active_proof["algo_id"]))
        _ingest_order_fills(client, allocator, entry_order_id)
        position_store.save_protection(
            LogicalPositionProtection(
                position_id=position_id,
                kind="attached_stop",
                algo_id=str(active_proof["algo_id"]),
                algo_client_order_id=str(active_proof["algo_client_order_id"]),
                quantity=float(active_proof["quantity"]),
                stop_loss=float(active_proof["stop_loss"]),
            )
        )
        _audit(
            audit_store,
            run_id,
            environment,
            "protected_open_confirmed",
            {"order_id": entry_order_id},
        )

        protection_service.amend_stop_condition(
            position_id,
            condition.id,
            expression={
                "type": "price_below",
                "symbol": INSTRUMENT,
                "value": float(amended_stop),
            },
            reason=f"{environment} lifecycle stop amendment",
        )

        # Model the durable fill checkpoint held by the running service before
        # it is interrupted. Restart recovery must process only fills newer
        # than this boundary, including the reduce submitted below.
        checkpoint_fills = client.get_fills_history(inst_type="SWAP", limit="100")
        if not checkpoint_fills or not str(checkpoint_fills[0].get("billId") or ""):
            raise RuntimeError("could not establish pre-interruption fill checkpoint")
        ExecutionCursorStore(db_path).complete(
            ExecutionFillService.FILL_STREAM_ID,
            high_water_id=str(checkpoint_fills[0]["billId"]),
        )

        manager = PositionManagerService(
            trade_store,
            dry_run=False,
            audit_store=audit_store,
            close_executor=executor,
            protection_service=protection_service,
        )
        reduce_result = manager.request_reduce(
            position_id,
            quantity=float(REDUCE_SIZE),
            reason=f"{environment} lifecycle partial reduce",
        )
        reduce_order_id = str(reduce_result.get("exchange_order_id") or "")
        if not reduce_order_id:
            raise RuntimeError(f"{environment} reduce was not submitted: {reduce_result}")
        owned_order_ids.add(reduce_order_id)
        _wait_for_order(client, reduce_order_id)

        # Deliberately interrupt before local fill ingestion or protection
        # restoration. Fresh stores/services must recover the confirmed partial
        # reduce through REST catch-up while the private order stream is live.
        interrupted = ExecutionFillService(
            client=OKXClient(),
            allocator=ExecutionAllocationService(
                TradeStore(db_path),
                LogicalPositionStore(db_path),
                AuditEventStore(db_path),
            ),
            enable_private_stream=True,
            rest_poll_interval=0,
        )
        interrupted_runner = DaemonRunner()
        interrupted_runner.register(interrupted)
        interrupted.setup()
        try:
            deadline = time.monotonic() + 20
            interrupted_status: dict = {}
            while time.monotonic() < deadline:
                interrupted.tick()
                interrupted_status = interrupted_runner.runtime.get_value(
                    "execution.fills.status"
                ) or {}
                recovered = LogicalPositionStore(db_path).get(position_id)
                if (
                    interrupted_status.get("caught_up")
                    and interrupted_status.get("websocket_connected")
                    and recovered is not None
                    and recovered.status == "open"
                    and Decimal(str(recovered.remaining_quantity)) == REDUCE_SIZE
                ):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError(
                    "interrupted partial-fill recovery did not converge through "
                    f"REST and private stream: {interrupted_status}"
                )

            recovered_protection = LogicalPositionStore(db_path).get_protection(
                position_id
            )
            if recovered_protection is not None:
                owned_algo_ids.add(recovered_protection.algo_id)

            # A second poll must not change logical quantity or create a second
            # allocation for the same exchange fill.
            interrupted.tick()
            recovered_again = LogicalPositionStore(db_path).get(position_id)
            allocations = LogicalPositionStore(db_path).list_allocations(position_id)
            reduce_allocations = [
                item for item in allocations if item.exchange_order_id == reduce_order_id
            ]
            if (
                recovered_again is None
                or Decimal(str(recovered_again.remaining_quantity)) != REDUCE_SIZE
                or len(reduce_allocations) != 1
            ):
                raise RuntimeError("restart recovery duplicated or lost partial-fill quantity")
        finally:
            interrupted.teardown()

        position_store = LogicalPositionStore(db_path)
        audit_store = AuditEventStore(db_path)
        allocator = ExecutionAllocationService(
            TradeStore(db_path), position_store, audit_store
        )
        protection_service = PositionProtectionService(client, position_store, audit_store)
        manager = PositionManagerService(
            TradeStore(db_path),
            dry_run=False,
            audit_store=audit_store,
            close_executor=executor,
            protection_service=protection_service,
        )
        reduced = position_store.get(position_id)
        if reduced is None or reduced.status != "open" or Decimal(
            str(reduced.remaining_quantity)
        ) != REDUCE_SIZE:
            raise RuntimeError("confirmed reduce did not leave exact logical remainder")
        resized = position_store.get_protection(position_id)
        if resized is None or Decimal(str(resized.quantity)) != REDUCE_SIZE:
            raise RuntimeError("restart recovery did not restore protection at reduced quantity")
        protection_service.verify_active(position_id)
        owned_algo_ids.add(resized.algo_id)
        _audit(
            audit_store,
            run_id,
            environment,
            "reduce_confirmed",
            {
                "order_id": reduce_order_id,
                "rest_caught_up": interrupted_status.get("caught_up", False),
                "private_stream_connected": interrupted_status.get(
                    "websocket_connected", False
                ),
                "allocation_count": len(reduce_allocations),
                "protection_algo_id": resized.algo_id,
            },
        )

        close_result = manager.request_close(
            position_id,
            reason=f"{environment} lifecycle cleanup close",
        )
        close_order_id = str(close_result.get("exchange_order_id") or "")
        if not close_order_id:
            raise RuntimeError(f"{environment} close was not submitted: {close_result}")
        owned_order_ids.add(close_order_id)
        _wait_for_order(client, close_order_id)
        _ingest_order_fills(client, allocator, close_order_id)
        closed = position_store.get(position_id)
        if closed is None or closed.status != "closed" or closed.remaining_quantity != 0:
            raise RuntimeError("confirmed close did not close logical position")
        _audit(
            audit_store,
            run_id,
            environment,
            "close_confirmed",
            {"order_id": close_order_id},
        )

        restarted = ExecutionFillService(
            client=OKXClient(),
            allocator=ExecutionAllocationService(
                TradeStore(db_path),
                LogicalPositionStore(db_path),
                AuditEventStore(db_path),
            ),
            protection_service=PositionProtectionService(
                OKXClient(),
                LogicalPositionStore(db_path),
                AuditEventStore(db_path),
            ),
        )
        runner = DaemonRunner()
        runner.register(restarted)
        restarted.tick()
        restart_status = runner.runtime.get_value("execution.fills.status")
        _audit(
            audit_store,
            run_id,
            environment,
            "restart_recovery_confirmed",
            {
                "idempotent_fills": restart_status.get("idempotent", 0),
                "caught_up": restart_status.get("caught_up", False),
            },
        )
        if any(
            Decimal(str(position.get("pos") or "0")) != 0
            for position in client.get_positions(inst_id=INSTRUMENT)
        ):
            raise RuntimeError(
                f"{environment} lifecycle left exchange exposure after close"
            )
        result = {
            "run_id": run_id,
            "environment": environment,
            "instrument": INSTRUMENT,
            "open_size": float(OPEN_SIZE),
            "reduce_size": float(REDUCE_SIZE),
            "final_status": closed.status,
            "restart_idempotent_fills": restart_status.get("idempotent", 0),
            "audit_db": db_path,
        }
        _audit(audit_store, run_id, environment, "completed", result)
        return result
    finally:
        disable_entry_order_placement()
        cleanup_actions.extend(
            _cleanup(
                client,
                owned_order_ids=owned_order_ids,
                owned_algo_ids=owned_algo_ids,
                cleanup_prefix=f"{prefix}x",
            )
        )
        _audit(
            audit_store,
            run_id,
            environment,
            "cleanup",
            {"actions": cleanup_actions},
        )
        disarm_order_placement()


def main() -> None:
    parser = argparse.ArgumentParser()
    confirmation = parser.add_mutually_exclusive_group(required=True)
    confirmation.add_argument("--confirm-demo-orders", action="store_true")
    confirmation.add_argument("--confirm-production-orders", action="store_true")
    parser.add_argument(
        "--db-path",
        default="",
        help="SQLite evidence path",
    )
    args = parser.parse_args()
    environment = "production" if args.confirm_production_orders else "demo"
    db_path = args.db_path or f"data/{environment}-lifecycle.db"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    result = run(db_path, environment=environment)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
