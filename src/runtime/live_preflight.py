"""Fail-closed validation before enabling order-capable runtime services."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from src.exchange.client import OKXClient
from src.runtime.lease import account_scope as build_account_scope
from src.trading.account_risk import AccountRiskStore
from src.trading.instrument_constraints import InstrumentConstraints
from src.trading.logical_position_store import LogicalPositionStore
from src.trading.position_protection import (
    PositionProtectionError,
    PositionProtectionService,
)
from src.trading.strategy_runtime import order_size, validate_strategy_for_execution
from src.trading.strategy_store import StrategyStore


class LivePreflightError(RuntimeError):
    """Raised when live/demo order execution cannot be armed safely."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Live startup preflight failed: " + "; ".join(errors))


@dataclass(frozen=True)
class LivePreflightReport:
    passed: bool
    armed: bool
    execution_mode: str
    account_level: str
    position_mode: str
    account_scope: str
    enabled_strategies: int
    risk_limits_enabled: bool
    entries_enabled: bool
    instruments: tuple[str, ...]
    checked_at: str

    def to_dict(self, *, armed: bool | None = None) -> dict[str, Any]:
        value = asdict(self)
        value["instruments"] = list(self.instruments)
        if armed is not None:
            value["armed"] = armed
        return value


def dry_run_preflight_report() -> dict[str, Any]:
    return {
        "passed": True,
        "armed": False,
        "execution_mode": "dry_run",
        "account_level": "",
        "position_mode": "",
        "account_scope": "",
        "enabled_strategies": 0,
        "risk_limits_enabled": False,
        "entries_enabled": False,
        "instruments": [],
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def run_live_preflight(
    *,
    client: OKXClient | None = None,
    strategy_store: StrategyStore | None = None,
    position_store: LogicalPositionStore | None = None,
    risk_store: AccountRiskStore | None = None,
    include_strategy: bool = True,
) -> LivePreflightReport:
    """Validate environment, account mode, strategies, and instruments."""
    errors = _validate_local_environment()
    if errors:
        raise LivePreflightError(errors)

    client = client or OKXClient()
    strategy_store = strategy_store or StrategyStore()
    position_store = position_store or LogicalPositionStore(strategy_store.db_path)
    risk_store = risk_store or AccountRiskStore(strategy_store.db_path)
    configured_flag = os.getenv("OKX_FLAG", "1")
    if str(client.flag) != configured_flag:
        raise LivePreflightError(["OKX client environment does not match OKX_FLAG"])

    try:
        account_configs = client.get_account_config()
    except Exception as exc:
        raise LivePreflightError([f"OKX account authentication failed: {exc}"]) from exc
    if len(account_configs) != 1:
        raise LivePreflightError(
            [f"OKX account config returned {len(account_configs)} records; expected 1"]
        )

    account_config = account_configs[0]
    account_level = str(account_config.get("acctLv") or "")
    position_mode = str(account_config.get("posMode") or "")
    account_uid = str(account_config.get("uid") or "")
    account_scope = ""
    if not account_uid:
        errors.append("OKX account config is missing uid")
    else:
        account_scope = build_account_scope(flag=configured_flag, uid=account_uid)
    if account_level not in {"2", "3", "4"}:
        errors.append(
            "OKX account must use Futures, Multi-currency margin, or Portfolio margin mode"
        )
    if position_mode != "net_mode":
        errors.append("OKX position mode must be net_mode for the current executor")

    risk_limits = risk_store.get()
    if risk_limits is None:
        errors.append("account risk limits are not configured in SQLite")
    elif not risk_limits.enabled:
        errors.append("account risk limits are disabled")
    else:
        try:
            risk_limits.validate()
        except ValueError as exc:
            errors.append(f"account risk limits: {exc}")

    enabled_strategies = strategy_store.list(enabled=True) if include_strategy else []
    instruments: set[str] = set()
    sizes_by_instrument: dict[str, list[str]] = {}
    for strategy in enabled_strategies:
        errors.extend(
            f"strategy {strategy.id}: {error}"
            for error in validate_strategy_for_execution(strategy, strategy_store)
        )
        for inst_id in strategy.target_instruments:
            instruments.add(inst_id)
            requested_size = order_size(strategy, inst_id)
            if requested_size is not None:
                sizes_by_instrument.setdefault(inst_id, []).append(requested_size)

    active_positions = position_store.list_active()
    for position in active_positions:
        if not position.inst_id:
            errors.append(f"active logical position {position.id} has no instrument")
        else:
            instruments.add(position.inst_id)

    for inst_id in sorted(instruments):
        try:
            payloads = client.get_instruments(inst_type="SWAP", inst_id=inst_id)
            if len(payloads) != 1:
                raise ValueError(f"expected one SWAP instrument, got {len(payloads)}")
            constraints = InstrumentConstraints.from_okx(payloads[0])
            if constraints.inst_id != inst_id:
                raise ValueError(f"returned instrument {constraints.inst_id!r}")
            constraints.validate_tradable()
            for requested_size in sizes_by_instrument.get(inst_id, []):
                constraints.normalize_size(requested_size)
        except Exception as exc:
            errors.append(f"instrument {inst_id}: {exc}")

    protection_service = PositionProtectionService(client, position_store)
    for position in active_positions:
        remaining = position.remaining_quantity or position.opened_quantity or 0.0
        if remaining <= 0:
            continue
        try:
            protection_service.verify_active(position.id)
        except PositionProtectionError as exc:
            errors.append(f"logical position {position.id} protection: {exc}")

    if errors:
        raise LivePreflightError(errors)

    return LivePreflightReport(
        passed=True,
        armed=False,
        execution_mode="demo" if configured_flag == "1" else "real",
        account_level=account_level,
        position_mode=position_mode,
        account_scope=account_scope,
        enabled_strategies=len(enabled_strategies),
        risk_limits_enabled=bool(risk_limits and risk_limits.enabled),
        entries_enabled=bool(risk_limits and risk_limits.entries_enabled),
        instruments=tuple(sorted(instruments)),
        checked_at=datetime.now(timezone.utc).isoformat(),
    )


def _validate_local_environment() -> list[str]:
    errors: list[str] = []
    for key in ("OKX_API_KEY", "OKX_API_SECRET", "OKX_PASSPHRASE"):
        if not os.getenv(key, "").strip():
            errors.append(f"{key} is required")
    flag = os.getenv("OKX_FLAG", "1")
    if flag not in {"0", "1"}:
        errors.append("OKX_FLAG must be '0' for real trading or '1' for demo trading")
    if os.getenv("MAYBECH_ARM_ORDERS", "0") != "1":
        errors.append("MAYBECH_ARM_ORDERS must be exactly '1' for --live startup")
    return errors
