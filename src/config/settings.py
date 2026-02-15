"""
Centralized settings loaded from .env file.

Usage:
    from src.config.settings import settings
    print(settings.OKX_API_KEY)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_env_path = Path(__file__).resolve().parents[2] / ".env"
load_dotenv(_env_path)


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default)


def _get_float(key: str, default: float = 0.0) -> float:
    return float(os.getenv(key, str(default)))


def _get_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


@dataclass(frozen=True)
class Settings:
    """Immutable application settings — populated from environment variables."""

    # OKX API
    OKX_API_KEY: str = field(default_factory=lambda: _get("OKX_API_KEY"))
    OKX_API_SECRET: str = field(default_factory=lambda: _get("OKX_API_SECRET"))
    OKX_PASSPHRASE: str = field(default_factory=lambda: _get("OKX_PASSPHRASE"))
    OKX_FLAG: str = field(default_factory=lambda: _get("OKX_FLAG", "1"))

    # Trading
    TRADING_PAIRS: list[str] = field(
        default_factory=lambda: _get("TRADING_PAIRS", "BTC-USDT").split(",")
    )
    CANDLE_INTERVAL: str = field(default_factory=lambda: _get("CANDLE_INTERVAL", "15m"))
    MAX_POSITION_RATIO: float = field(
        default_factory=lambda: _get_float("MAX_POSITION_RATIO", 0.1)
    )
    TRADE_QUANTITY_ETH: float = field(
        default_factory=lambda: _get_float("TRADE_QUANTITY_ETH", 0.1)
    )

    # Momentum Strategy
    MOMENTUM_K_LONG: float = field(
        default_factory=lambda: _get_float("MOMENTUM_K_LONG", 10.0)
    )
    MOMENTUM_K_SHORT: float = field(
        default_factory=lambda: _get_float("MOMENTUM_K_SHORT", 5.0)
    )
    PRICE_GAP_THRESHOLD: float = field(
        default_factory=lambda: _get_float("PRICE_GAP_THRESHOLD", 3.0)
    )


    # Risk management (bear-market bias)
    STOP_LOSS_LONG_PCT: float = field(
        default_factory=lambda: _get_float("STOP_LOSS_LONG_PCT", 0.02)
    )
    STOP_LOSS_SHORT_PCT: float = field(
        default_factory=lambda: _get_float("STOP_LOSS_SHORT_PCT", 0.04)
    )
    TAKE_PROFIT_LONG_PCT: float = field(
        default_factory=lambda: _get_float("TAKE_PROFIT_LONG_PCT", 0.03)
    )
    TAKE_PROFIT_SHORT_PCT: float = field(
        default_factory=lambda: _get_float("TAKE_PROFIT_SHORT_PCT", 0.05)
    )

    # Backtesting
    BACKTEST_MIN_WIN_RATE: float = field(
        default_factory=lambda: _get_float("BACKTEST_MIN_WIN_RATE", 0.55)
    )
    BACKTEST_MIN_RETURN_RATE: float = field(
        default_factory=lambda: _get_float("BACKTEST_MIN_RETURN_RATE", 0.10)
    )
    BACKTEST_LOOKBACK_DAYS: int = field(
        default_factory=lambda: _get_int("BACKTEST_LOOKBACK_DAYS", 30)
    )

    # Notifications — LINE Bot
    LINE_CHANNEL_ACCESS_TOKEN: str = field(
        default_factory=lambda: _get("LINE_CHANNEL_ACCESS_TOKEN")
    )
    LINE_CHANNEL_SECRET: str = field(default_factory=lambda: _get("LINE_CHANNEL_SECRET"))
    LINE_USER_ID: str = field(default_factory=lambda: _get("LINE_USER_ID"))

    # Notifications — Email
    EMAIL_SMTP_HOST: str = field(
        default_factory=lambda: _get("EMAIL_SMTP_HOST", "smtp.gmail.com")
    )
    EMAIL_SMTP_PORT: int = field(default_factory=lambda: _get_int("EMAIL_SMTP_PORT", 587))
    EMAIL_SENDER: str = field(default_factory=lambda: _get("EMAIL_SENDER"))
    EMAIL_PASSWORD: str = field(default_factory=lambda: _get("EMAIL_PASSWORD"))
    EMAIL_RECEIVER: str = field(default_factory=lambda: _get("EMAIL_RECEIVER"))

    # Logging
    LOG_LEVEL: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))


# Singleton instance — import this everywhere
settings = Settings()
