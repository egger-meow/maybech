"""
Centralized settings loaded from .env file.

Usage:
    from src.config.settings import settings
    print(settings.OKX_API_KEY)
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values, load_dotenv

# Load .env from project root. Snapshot what was already set in the process
# environment and what the .env file itself declares *before* loading it, so
# resolve_db_path_diagnostics() can later report which source actually won —
# a process-level env var always takes precedence over .env (dotenv does not
# override existing variables), and that precedence is otherwise invisible.
_env_path = Path(__file__).resolve().parents[2] / ".env"
_pre_dotenv_environ_keys = frozenset(os.environ.keys())
_dotenv_file_values = dotenv_values(_env_path)
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


def load_okx_connection_settings(
    flag: str | None = None, *, runtime_mode: str | None = None
) -> OKXConnectionSettings:
    """Select one complete credential set for the requested OKX environment."""
    if runtime_mode is not None:
        from src.runtime.mode import RuntimeMode, parse_runtime_mode

        mode = parse_runtime_mode(runtime_mode)
        if mode is RuntimeMode.SIMULATION:
            raise ValueError("simulation mode does not select OKX credentials")
        selected_flag = mode.okx_flag
        if flag is not None and str(flag) != selected_flag:
            raise ValueError("runtime mode and OKX credential environment conflict")
    else:
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
    DEMO_MAYBECH_DB_PATH: str = field(
        default_factory=lambda: _get("DEMO_MAYBECH_DB_PATH", "data/demo_trades.db")
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

# Pristine MAYBECH_DB_PATH as loaded from the environment, captured before
# activate_db_path() ever has a chance to overwrite settings.MAYBECH_DB_PATH
# with the demo path. resolve_db_path() must stay idempotent even if
# activate_db_path() runs more than once in a process (e.g. tests exercising
# multiple modes), so the non-demo case always reads this constant rather
# than the (possibly already-pinned) live field.
_default_maybech_db_path = settings.MAYBECH_DB_PATH


def resolve_db_path(mode: Any = None) -> str:
    """Select the SQLite path for a runtime mode.

    Demo trades against OKX's demo instance and must never land in the same
    database as Simulation or either live mode (see docs/deployment.md,
    "Demo and Real must not share one SQLite database") — the same
    mode-scoped separation ``load_okx_connection_settings`` already applies
    to credentials via ``OKX_FLAG``. Every other mode shares
    ``MAYBECH_DB_PATH``.
    """
    from src.runtime.mode import RuntimeMode, parse_runtime_mode

    if parse_runtime_mode(mode) is RuntimeMode.DEMO:
        return settings.DEMO_MAYBECH_DB_PATH
    return _default_maybech_db_path


def resolve_db_path_diagnostics(
    db_path: str | None = None, *, mode: Any = None
) -> dict[str, Any]:
    """Report exactly where the active SQLite path came from and whether it is new.

    Every store falls back to ``settings.MAYBECH_DB_PATH`` when constructed
    without an explicit path (see docs/storage.md), but that value is resolved
    once, silently, at import time. A process-level environment variable wins
    over ``.env`` without any indication of that happening, which previously
    made "which database is this run actually touching" a matter of
    archaeology. Call this once at startup and log/expose the result instead.
    """
    from src.runtime.mode import RuntimeMode, parse_runtime_mode

    env_key = (
        "DEMO_MAYBECH_DB_PATH"
        if parse_runtime_mode(mode) is RuntimeMode.DEMO
        else "MAYBECH_DB_PATH"
    )
    configured = db_path or resolve_db_path(mode)
    resolved_path = str(Path(configured).resolve())
    if env_key in _pre_dotenv_environ_keys:
        source = "process_environment"
    elif env_key in _dotenv_file_values:
        source = "dotenv_file"
    else:
        source = "default"
    return {
        "configured_path": configured,
        "resolved_path": resolved_path,
        "source": source,
        "env_key": env_key,
        "existed_before_process": Path(resolved_path).exists(),
    }


def activate_db_path(mode: Any = None) -> dict[str, Any]:
    """Pin the process-wide SQLite path for a runtime mode before any store is built.

    ``settings.MAYBECH_DB_PATH`` is what every store's ``db_path or
    settings.MAYBECH_DB_PATH`` fallback resolves to — including the dozens of
    stores the API layer (``src/api/app.py``) constructs with no explicit
    path per request. Call this exactly once, at startup, before the first
    store is constructed, so that fallback (and the whole process) observes
    the demo database under ``RuntimeMode.DEMO`` and the shared database
    everywhere else, instead of the two drifting apart.
    """
    diagnostics = resolve_db_path_diagnostics(mode=mode)
    object.__setattr__(settings, "MAYBECH_DB_PATH", diagnostics["configured_path"])
    return diagnostics
