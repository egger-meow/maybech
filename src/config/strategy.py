"""SQLite-backed runtime strategy configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.trading.strategy_store import StrategyRecord, StrategyStore


DEFAULT_STRATEGY_ID = "momentum_swap"


@dataclass
class MomentumConfig:
    k_long: float = 12.0
    k_short: float = 10.0
    gap_threshold: float = 10.0
    stop_win_ratio: float = 1.0
    stop_win_vol_ratio: bool = False


@dataclass
class StrategyConfig:
    """Parameters consumed by the current momentum runtime."""

    momentum: MomentumConfig = field(default_factory=MomentumConfig)
    active_strategy: str = "momentum"
    target_instruments: list[str] = field(
        default_factory=lambda: ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
    )
    timeframe: str = "1m"
    order_size_contracts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "StrategyConfig":
        return cls()

    @classmethod
    def from_record(cls, record: "StrategyRecord") -> "StrategyConfig":
        entry = record.entry_signal
        rules = record.default_rules
        metadata = record.metadata
        raw_sizes = metadata.get("order_size_contracts", {})
        sizes = (
            {str(key): str(value) for key, value in raw_sizes.items()}
            if isinstance(raw_sizes, dict)
            else {}
        )
        return cls(
            momentum=MomentumConfig(
                k_long=float(entry.get("k_long", 12.0)),
                k_short=float(entry.get("k_short", 10.0)),
                gap_threshold=float(entry.get("gap_threshold", 10.0)),
                stop_win_ratio=float(rules.get("stop_win_ratio", 1.0)),
                stop_win_vol_ratio=bool(rules.get("stop_win_vol_ratio", False)),
            ),
            active_strategy=record.id,
            target_instruments=[str(item) for item in record.target_instruments],
            timeframe=str(entry.get("timeframe", "1m")),
            order_size_contracts=sizes,
        )

    @classmethod
    def load(cls, db_path: str | None = None) -> "StrategyConfig":
        from src.trading.strategy_store import StrategyStore

        store = StrategyStore(db_path)
        record = ensure_default_strategy(store)
        return cls.from_record(record)


def default_strategy_definition() -> dict[str, Any]:
    """Return the one-time seed for a new database."""
    config = StrategyConfig.default()
    return {
        "id": DEFAULT_STRATEGY_ID,
        "name": "Momentum Swap",
        "kind": "momentum",
        "enabled": True,
        "target_instruments": config.target_instruments,
        "entry_signal": {
            "type": "volume_price_gap",
            "timeframe": config.timeframe,
            "k_long": config.momentum.k_long,
            "k_short": config.momentum.k_short,
            "gap_threshold": config.momentum.gap_threshold,
        },
        "default_rules": {
            "stop_win_ratio": config.momentum.stop_win_ratio,
            "stop_win_vol_ratio": config.momentum.stop_win_vol_ratio,
        },
        "metadata": {
            "order_size_contracts": {},
            "seeded_from_code_defaults": True,
        },
    }


def ensure_default_strategy(store: "StrategyStore") -> "StrategyRecord":
    return store.ensure(**default_strategy_definition())
