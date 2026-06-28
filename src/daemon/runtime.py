"""Factory helpers for Maybech daemon runtime instances."""

from __future__ import annotations

from src.daemon.account_service import AccountSnapshotService
from src.daemon.btc_regime_service import BTCRegimeService
from src.daemon.execution_fill_service import ExecutionFillService
from src.daemon.notificator_service import NotificatorService
from src.daemon.position_intent_service import PositionIntentService
from src.daemon.position_manager_service import PositionManagerService
from src.daemon.service import DaemonRunner
from src.daemon.strategy_service import StrategyService
from src.exchange.client import (
    arm_order_placement,
    disarm_order_placement,
    enable_entry_order_placement,
)
from src.runtime.live_preflight import dry_run_preflight_report, run_live_preflight
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.strategy_store import StrategyStore
from src.trading.trade_store import TradeStore
from src.trading.execution_allocation import ExecutionAllocationService
from src.trading.account_risk import AccountRiskStore


def create_default_runner(*, dry_run: bool = True, include_strategy: bool = True) -> DaemonRunner:
    """Create the standard daemon runner used by UI, API, and headless modes."""
    disarm_order_placement()
    runner = DaemonRunner()
    store = TradeStore()
    risk_store = AccountRiskStore(store.db_path)
    if dry_run:
        preflight_status = dry_run_preflight_report()
    else:
        report = run_live_preflight(
            strategy_store=StrategyStore(store.db_path),
            position_store=LogicalPositionStore(store.db_path),
            risk_store=risk_store,
            include_strategy=include_strategy,
        )
        preflight_status = report.to_dict(armed=False)
    runner.runtime.set_value("runtime.live_preflight", preflight_status)
    
    runner.register(AccountSnapshotService())
    runner.register(BTCRegimeService())
    runner.register(PositionIntentService())
    
    # Register the dynamic position rule manager
    runner.register(PositionManagerService(store=store, dry_run=dry_run))
    runner.register(
        ExecutionFillService(
            allocator=ExecutionAllocationService(trade_store=store),
            enable_private_stream=not dry_run,
            rest_poll_interval=5.0,
        )
    )
    
    if include_strategy:
        runner.register(StrategyService(dry_run=dry_run, trade_store=store))
    runner.register(NotificatorService())
    if not dry_run:
        required_services = {
            "account",
            "btc_regime",
            "position_manager",
            "execution_fills",
        }
        if include_strategy:
            required_services.add("strategy")
        runner.add_shutdown_callback(disarm_order_placement)
        runner.setup_services(required_services=required_services)
        arm_order_placement(preflight_passed=report.passed)
        limits = risk_store.get()
        if limits is not None and limits.entries_enabled:
            enable_entry_order_placement()
        runner.runtime.set_value(
            "runtime.live_preflight",
            report.to_dict(armed=True),
        )
    return runner
