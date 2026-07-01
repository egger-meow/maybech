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


def _get_int(key: str, default: int = 0) -> int:
    return int(os.getenv(key, str(default)))


@dataclass(frozen=True)
class OKXConnectionSettings:
    flag: str
    api_key: str
    api_secret: str
    passphrase: str

    @property
    def execution_mode(self) -> str:
        return "demo" if self.flag == "1" else "production"


def load_okx_connection_settings(flag: str | None = None) -> OKXConnectionSettings:
    """Select one complete credential set for the requested OKX environment."""
    selected_flag = _get("OKX_FLAG", "1") if flag is None else str(flag)
    if selected_flag == "1":
        return OKXConnectionSettings(
            flag=selected_flag,
            api_key=_get("DEMO_OKX_API_KEY"),
            api_secret=_get("DEMO_OKX_API_SECRET"),
            passphrase=_get("DEMO_OKX_PASSPHRASE"),
        )
    if selected_flag == "0":
        return OKXConnectionSettings(
            flag=selected_flag,
            api_key=_get("OKX_API_KEY"),
            api_secret=_get("OKX_API_SECRET"),
            passphrase=_get("OKX_PASSPHRASE"),
        )
    raise ValueError("OKX_FLAG must be '0' for production or '1' for demo")


@dataclass(frozen=True)
class Settings:
    """Immutable application settings — populated from environment variables."""

    # OKX API
    # These public fields are the credentials selected by OKX_FLAG. Callers do
    # not choose a credential namespace independently from the endpoint mode.
    OKX_API_KEY: str = field(
        default_factory=lambda: load_okx_connection_settings().api_key
    )
    OKX_API_SECRET: str = field(
        default_factory=lambda: load_okx_connection_settings().api_secret
    )
    OKX_PASSPHRASE: str = field(
        default_factory=lambda: load_okx_connection_settings().passphrase
    )
    OKX_FLAG: str = field(default_factory=lambda: _get("OKX_FLAG", "1"))

    # Persistence
    MAYBECH_DB_PATH: str = field(
        default_factory=lambda: _get("MAYBECH_DB_PATH", "data/trades.db")
    )
    MAYBECH_CORS_ORIGINS: list[str] = field(
        default_factory=lambda: [
            origin.strip()
            for origin in _get(
                "MAYBECH_CORS_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000",
            ).split(",")
            if origin.strip()
        ]
    )
    MAYBECH_API_TOKEN: str = field(
        default_factory=lambda: _get("MAYBECH_API_TOKEN")
    )

    # Notifications — LINE Bot
    LINE_CHANNEL_ACCESS_TOKEN: str = field(
        default_factory=lambda: _get("LINE_CHANNEL_ACCESS_TOKEN")
    )
    LINE_CHANNEL_SECRET: str = field(default_factory=lambda: _get("LINE_CHANNEL_SECRET"))
    LINE_USER_ID: str = field(default_factory=lambda: _get("LINE_USER_ID"))

    NOTIFICATION_COOLDOWN_SECONDS: int = field(
        default_factory=lambda: _get_int("NOTIFICATION_COOLDOWN_SECONDS", 300)
    )

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
