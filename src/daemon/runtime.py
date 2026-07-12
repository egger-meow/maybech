"""Factory helpers for Maybech daemon runtime instances."""

from __future__ import annotations

import os

from src.config.settings import activate_db_path
from src.daemon.account_service import AccountSnapshotService
from src.daemon.btc_regime_service import BTCRegimeService
from src.daemon.execution_fill_service import ExecutionFillService
from src.daemon.lifecycle_notification_service import LifecycleNotificationService
from src.daemon.market_intelligence_service import MarketIntelligenceSyncService
from src.daemon.position_intent_service import PositionIntentService
from src.daemon.position_manager_service import PositionManagerService
from src.daemon.service import DaemonRunner
from src.daemon.strategy_service import StrategyService
from src.data.simulation_market import SimulationMarketClient
from src.data.candles import CandleManager
from src.trading.executor import Executor
from src.exchange.client import (
    OKXClient,
    arm_order_placement,
    disarm_order_placement,
)
from src.runtime.live_preflight import simulation_preflight_report, run_live_preflight
from src.runtime.mode import RuntimeMode, parse_runtime_mode
from src.runtime.lease import RuntimeLease, RuntimeLeaseService
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.strategy_store import StrategyStore
from src.trading.trade_store import TradeStore
from src.trading.execution_allocation import ExecutionAllocationService
from src.trading.account_risk import AccountRiskStore
from src.trading.audit_event_store import AuditEventStore
from src.utils.logger import setup_logger


logger = setup_logger(__name__)


def create_default_runner(
    *,
    mode: RuntimeMode | str | None = None,
    dry_run: bool | None = None,
    include_strategy: bool = True,
) -> DaemonRunner:
    """Create the standard daemon runner used by UI, API, and headless modes."""
    if mode is None:
        resolved_mode = RuntimeMode.SIMULATION if dry_run is not False else (
            RuntimeMode.DEMO if os.getenv("OKX_FLAG", "1") == "1" else RuntimeMode.LIVE_ARMED
        )
    else:
        resolved_mode = parse_runtime_mode(mode)
        if dry_run is not None and dry_run != (resolved_mode is RuntimeMode.SIMULATION):
            raise ValueError("mode conflicts with deprecated dry_run argument")
    simulation = resolved_mode is RuntimeMode.SIMULATION
    order_capable = resolved_mode.submits_orders
    live_safe = resolved_mode is RuntimeMode.LIVE_SAFE
    disarm_order_placement()
    # Resolve, pin, and log the DB path before any store touches disk. Demo
    # gets its own database (DEMO_MAYBECH_DB_PATH) so it can never mix state
    # with Simulation/Live (MAYBECH_DB_PATH) even if a process-level env var
    # silently overrides .env (dotenv never overrides an already-set
    # variable) — previously that made "which database is this run actually
    # using" invisible until state from an unexpected file showed up
    # mid-session. Pinning settings.MAYBECH_DB_PATH here (not just logging it)
    # also keeps every no-arg store the API layer constructs per request in
    # sync with the store this runner builds below.
    db_path_info = activate_db_path(resolved_mode)
    logger.info(
        "Maybech DB path resolved to %s (source=%s, env=%s, existed_before_process=%s)",
        db_path_info["resolved_path"],
        db_path_info["source"],
        db_path_info["env_key"],
        db_path_info["existed_before_process"],
    )
    runner = DaemonRunner()
    store = TradeStore()
    lease = RuntimeLease(db_path=store.db_path)
    try:
        lease.acquire()
        risk_store = AccountRiskStore(store.db_path)
        if not simulation:
            risk_store.set_entries_enabled(False)
        if simulation:
            preflight_status = simulation_preflight_report()
        else:
            report = run_live_preflight(
                strategy_store=StrategyStore(store.db_path),
                position_store=LogicalPositionStore(store.db_path),
                risk_store=risk_store,
                include_strategy=include_strategy and not live_safe,
                runtime_mode=resolved_mode,
            )
            lease.bind_account_scope(report.account_scope)
            preflight_status = report.to_dict(armed=False)
        preflight_status = {**preflight_status, "db_path": db_path_info}
    except Exception:
        lease.release()
        raise
    try:
        runner.runtime.set_value("runtime.live_preflight", preflight_status)
        lease_service = RuntimeLeaseService(
            lease,
            before_release=disarm_order_placement,
        )
        runner.register(lease_service)
        simulation_client = SimulationMarketClient() if simulation else None
        if not simulation:
            runner.register(AccountSnapshotService(position_store=LogicalPositionStore(store.db_path)))
        runner.register(BTCRegimeService(client=simulation_client))
        # Registered in every mode including simulation and never in
        # required_services: a provider outage degrades its own metrics, not
        # runtime startup. Its OKX-scoped sub-provider gets no client in
        # simulation (matching /market/overview's "no live feed" gating) and
        # reports itself unconfigured rather than failing every tick.
        runner.register(
            MarketIntelligenceSyncService(exchange_client=None if simulation else OKXClient())
        )
        runner.register(PositionIntentService())
        if not live_safe:
            runner.register(
                PositionManagerService(
                    store=store,
                    dry_run=simulation,
                    close_executor=(
                        Executor(simulation_client, dry_run=True) if simulation_client else None
                    ),
                    candle_manager=CandleManager(simulation_client) if simulation_client else None,
                )
            )
        if not simulation:
            runner.register(
                ExecutionFillService(
                    allocator=ExecutionAllocationService(trade_store=store),
                    # The private order stream is a hard correctness requirement,
                    # not an optional convenience: new entries stay blocked until
                    # REST catch-up is current AND this stream is connected (see
                    # README "Run Locally"). It must never be skippable — a
                    # bypass here would make execution_fills report healthy
                    # without ever actually confirming fills in real time.
                    enable_private_stream=True,
                    rest_poll_interval=5.0,
                    allow_order_mutations=order_capable,
                )
            )
        should_register_strategy = include_strategy and not live_safe and preflight_status.get("passed", False)
        if should_register_strategy:
            runner.register(
                StrategyService(
                    dry_run=simulation,
                    trade_store=store,
                    client=simulation_client,
                )
            )
        runner.register(
            LifecycleNotificationService(
                audit_store=AuditEventStore(store.db_path),
            )
        )
        lease_service.setup()
        if not simulation:
            required_services = {
                "runtime_lease",
                "account",
                "btc_regime",
                "execution_fills",
            }
            if not live_safe:
                required_services.add("position_manager")
            if should_register_strategy:
                required_services.add("strategy")
            runner.setup_services(required_services=required_services)
            should_arm_orders = order_capable and report.passed
            if should_arm_orders:
                arm_order_placement(preflight_passed=True)
            else:
                disarm_order_placement()
            runner.runtime.set_value(
                "runtime.live_preflight",
                {**report.to_dict(armed=should_arm_orders), "db_path": db_path_info},
            )
    except Exception:
        disarm_order_placement()
        try:
            if lease.held:
                runner.teardown_services()
        finally:
            if lease.held:
                lease.release()
        raise
    return runner
