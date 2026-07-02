"""First-class runtime execution modes and compatibility parsing."""

from __future__ import annotations

import os
from enum import StrEnum


class RuntimeMode(StrEnum):
    SIMULATION = "simulation"
    DEMO = "demo"
    LIVE_SAFE = "live_safe"
    LIVE_ARMED = "live_armed"

    @property
    def touches_exchange(self) -> bool:
        return self is not RuntimeMode.SIMULATION

    @property
    def submits_orders(self) -> bool:
        return self in {RuntimeMode.DEMO, RuntimeMode.LIVE_ARMED}

    @property
    def okx_flag(self) -> str | None:
        if self is RuntimeMode.DEMO:
            return "1"
        if self in {RuntimeMode.LIVE_SAFE, RuntimeMode.LIVE_ARMED}:
            return "0"
        return None


def parse_runtime_mode(value: str | RuntimeMode | None) -> RuntimeMode:
    if value is None:
        return RuntimeMode.SIMULATION
    if isinstance(value, RuntimeMode):
        return value
    normalized = value.strip().lower().replace("-", "_")
    aliases = {"dry_run": "simulation", "dryrun": "simulation", "real": "live_armed"}
    try:
        return RuntimeMode(aliases.get(normalized, normalized))
    except ValueError as exc:
        choices = ", ".join(mode.value for mode in RuntimeMode)
        raise ValueError(f"runtime mode must be one of: {choices}") from exc


def legacy_live_mode() -> RuntimeMode:
    """Map deprecated --live using the historical OKX_FLAG environment."""
    flag = os.getenv("OKX_FLAG", "1")
    if flag == "1":
        return RuntimeMode.DEMO
    if flag == "0":
        return RuntimeMode.LIVE_ARMED
    raise ValueError("OKX_FLAG must be '0' or '1' when using deprecated --live")
