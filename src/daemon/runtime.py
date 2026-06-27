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
from src.trading.trade_store import TradeStore
from src.trading.execution_allocation import ExecutionAllocationService


def create_default_runner(*, dry_run: bool = True, include_strategy: bool = True) -> DaemonRunner:
    """Create the standard daemon runner used by UI, API, and headless modes."""
    runner = DaemonRunner()
    store = TradeStore()
    
    runner.register(AccountSnapshotService())
    runner.register(BTCRegimeService())
    runner.register(PositionIntentService())
    
    # Register the dynamic position rule manager
    runner.register(PositionManagerService(store=store, dry_run=dry_run))
    runner.register(
        ExecutionFillService(
            allocator=ExecutionAllocationService(trade_store=store)
        )
    )
    
    if include_strategy:
        runner.register(StrategyService(dry_run=dry_run, trade_store=store))
    runner.register(NotificatorService())
    return runner
