"""
Strategy Configuration Loader.

Loads mutable parameters from `strategy_params.json`.
"""

from typing import Any, Dict
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class MomentumConfig:
    """Configuration for Momentum Strategy."""
    k_long: float
    k_short: float
    gap_threshold: float
    stop_win_ratio: float = 1.0
    stop_win_vol_ratio: bool = False

    @classmethod
    def default(cls) -> "MomentumConfig":
        return cls(
            k_long=settings.MOMENTUM_K_LONG,
            k_short=settings.MOMENTUM_K_SHORT,
            gap_threshold=settings.PRICE_GAP_THRESHOLD,
            stop_win_ratio=1.0,
            stop_win_vol_ratio=False,
        )


@dataclass
class StrategyConfig:
    """Global Strategy Configuration holding all strategy parameters."""
    momentum: MomentumConfig

    @classmethod
    def default(cls) -> "StrategyConfig":
        """Return default configuration."""
        return cls(
            momentum=MomentumConfig.default()
        )

    def save(self) -> None:
        """Save current config to JSON."""
        path = Path("src/config/strategy_params.json")
        try:
            with open(path, "w") as f:
                json.dump(asdict(self), f, indent=4)
            logger.info("Saved strategy config to %s", path)
        except Exception as e:
            logger.error("Failed to save strategy config: %s", e)

    @classmethod
    def load(cls) -> "StrategyConfig":
        """Load from JSON or return default."""
        path = Path("src/config/strategy_params.json")
        if not path.exists():
            return cls.default()
        
        try:
            with open(path, "r") as f:
                data = json.load(f)
            
            # Helper to safely load submodule configs
            # If "momentum" key is missing, or entire file is old format, fallback safely
            momentum_data = data.get("momentum")
            if not momentum_data:
                # Attempt to read old flat format if it exists, else default
                # Old format had k_long, k_short at root
                # If these keys are present and momentum is not, assume migration
                if "k_long" in data:
                    momentum_conf = MomentumConfig(
                        k_long=data.get("k_long", settings.MOMENTUM_K_LONG),
                        k_short=data.get("k_short", settings.MOMENTUM_K_SHORT),
                        gap_threshold=data.get("gap_threshold", settings.PRICE_GAP_THRESHOLD)
                    )
                else:
                    momentum_conf = MomentumConfig.default()
            else:
                momentum_conf = MomentumConfig(**momentum_data)

            return cls(momentum=momentum_conf)

        except Exception as e:
            logger.warning("Failed to load strategy config, using defaults: %s", e)
            return cls.default()
