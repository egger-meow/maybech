"""
Strategy Configuration Loader.

Loads mutable parameters from `strategy_params.json`.
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

from src.config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class StrategyConfig:
    """Mutable strategy parameters."""
    k_long: float
    k_short: float
    gap_threshold: float
    # Add other params here as needed
    
    @classmethod
    def default(cls) -> "StrategyConfig":
        """Return default configuration."""
        return cls(
            k_long=settings.MOMENTUM_K_LONG,   # Fallback to .env default if json missing
            k_short=settings.MOMENTUM_K_SHORT,
            gap_threshold=settings.PRICE_GAP_THRESHOLD,
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
            return cls(**data)
        except Exception as e:
            logger.warning("Failed to load strategy config, using defaults: %s", e)
            return cls.default()
