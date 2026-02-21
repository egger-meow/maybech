"""
Notificator configuration handler.
"""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.config.settings import settings

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("src/config/notificator_config.json")


@dataclass
class NotificatorConfig:
    """Configuration for the price-level proximity notificator."""

    enabled: bool = True
    check_interval: int = 60  # seconds
    timeframes: list[str] = field(default_factory=lambda: ["1H", "4H", "1D"])
    candle_limit: int = 100
    proximity_thresholds: dict[str, float] = field(default_factory=lambda: {
        "BTC-USDT-SWAP": 200.0,
        "ETH-USDT-SWAP": 20.0,
        "DEFAULT": 50.0
    })
    min_significance: float = 0.3
    cooldown_minutes: int = 30
    peak_valley_window: int = 2

    @classmethod
    def load(cls) -> "NotificatorConfig":
        """Load from JSON, with fallback to environment variables and defaults."""
        data = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r") as f:
                    data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load notificator config: {e}")

        # Construct from dictionary, allowing defaults to fill gaps
        config = cls(**data)

        # Env var overrides
        if settings.NOTIFICATOR_ENABLED is not None:
            # We'll assume the settings object is updated to include this in the next step
            config.enabled = bool(settings.NOTIFICATOR_ENABLED)

        return config

    def save(self) -> None:
        """Save current configuration to JSON."""
        CONFIG_PATH.parent.mkdir(exist_ok=True)
        try:
            with open(CONFIG_PATH, "w") as f:
                # Convert to dict, handle field default_factory if necessary
                d = {k: v for k, v in self.__dict__.items()}
                json.dump(d, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save notificator config: {e}")
