"""
Centralized logging setup.

All modules should use:
    from src.utils.logger import setup_logger
    logger = setup_logger(__name__)

Logs go to both console and logs/ directory.
"""

import logging
import sys
from pathlib import Path

from src.config.settings import settings

_LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
_LOG_DIR.mkdir(exist_ok=True)


def setup_logger(name: str) -> logging.Logger:
    """Create a logger that writes to console + file.

    Args:
        name: Usually __name__ of the calling module.
    """
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # already configured

    logger.setLevel(getattr(logging, settings.LOG_LEVEL, logging.INFO))

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler - DISABLED for TUI (Textual handles its own output)
    # console = logging.StreamHandler(sys.stdout)
    # console.setFormatter(fmt)
    # logger.addHandler(console)

    # File handler
    file_handler = logging.FileHandler(_LOG_DIR / "maybech.log", encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger
