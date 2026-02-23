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
class PeakValleyConfig:
    enabled: bool = True
    timeframes: list[str] = field(default_factory=lambda: ["1H", "4H", "1D"])
    window: int = 2
    min_significance: float = 0.3
    cooldown_minutes: int = 30
    proximity_thresholds: dict[str, float] = field(default_factory=lambda: {
        "BTC-USDT-SWAP": 200.0,
        "ETH-USDT-SWAP": 20.0,
        "DEFAULT": 50.0
    })

@dataclass
class FluctuationConfig:
    enabled: bool = True
    timeframes: list[str] = field(default_factory=lambda: ["1m"])
    window_minutes: int = 15
    cooldown_minutes: int = 30
    thresholds_pct: dict[str, float] = field(default_factory=lambda: {
        "BTC-USDT-SWAP": 2.0,
        "ETH-USDT-SWAP": 3.0,
        "DEFAULT": 5.0
    })

@dataclass
class FeaturesConfig:
    peak_valley: PeakValleyConfig = field(default_factory=PeakValleyConfig)
    fluctuation: FluctuationConfig = field(default_factory=FluctuationConfig)

@dataclass
class NotificatorConfig:
    """Configuration for the notificator service."""

    enabled: bool = True
    check_interval: int = 60  # seconds
    candle_limit: int = 150
    features: FeaturesConfig = field(default_factory=FeaturesConfig)

    @classmethod
    def load(cls) -> "NotificatorConfig":
        """Load from JSON, with fallback to environment variables and defaults."""
        data = {}
        if CONFIG_PATH.exists():
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load notificator config: {e}")

        # Extract features data
        features_data = data.pop("features", {})
        pv_data = features_data.get("peak_valley", {})
        fl_data = features_data.get("fluctuation", {})

        import dataclasses

        # Filter kwargs to avoid TypeError on unknown keys
        pv_keys = {f.name for f in dataclasses.fields(PeakValleyConfig)}
        pv_config = PeakValleyConfig(**{k: v for k, v in pv_data.items() if k in pv_keys})

        fl_keys = {f.name for f in dataclasses.fields(FluctuationConfig)}
        fl_config = FluctuationConfig(**{k: v for k, v in fl_data.items() if k in fl_keys})

        features_config = FeaturesConfig(peak_valley=pv_config, fluctuation=fl_config)

        cls_keys = {f.name for f in dataclasses.fields(cls)}
        config_kwargs = {k: v for k, v in data.items() if k in cls_keys}
        
        config = cls(features=features_config, **config_kwargs)

        # Env var overrides
        if settings.NOTIFICATOR_ENABLED is not None:
            config.enabled = bool(settings.NOTIFICATOR_ENABLED)

        return config

    def save(self) -> None:
        """Save current configuration to JSON."""
        CONFIG_PATH.parent.mkdir(exist_ok=True)
        try:
            import dataclasses
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                d = dataclasses.asdict(self)
                json.dump(d, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save notificator config: {e}")
