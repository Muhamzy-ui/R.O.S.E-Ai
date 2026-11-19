"""
R.O.S.E (Project Mirror) - Logging Setup
Rotating file log (logs/rose.log, 5MB x 3) + clean console handler.
"""

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_SECRET_URL_RE = re.compile(
    r"(bot)?\d{7,12}:[A-Za-z0-9_-]{28,55}|AIza[0-9A-Za-z-_]{35}|sk-[a-zA-Z0-9]{32,}|postgres(?:ql)?://[^:\s]+:[^@\s]+@"
)


class _SecretRedactFilter(logging.Filter):
    """Redacts API keys and Telegram bot tokens from log output."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            if _SECRET_URL_RE.search(msg):
                record.msg = _SECRET_URL_RE.sub("[REDACTED_SECRET]", msg)
                record.args = ()
        except Exception:
            pass
        return True

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
ROSE_LOG_FILE = LOGS_DIR / "rose.log"

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger("rose")

    if _CONFIGURED:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    formatter = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 1. Rotating File Handler: 5MB x 3 backups
    secret_filter = _SecretRedactFilter()
    file_handler = RotatingFileHandler(
        str(ROSE_LOG_FILE),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(secret_filter)
    logger.addHandler(file_handler)

    # 2. Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_formatter = logging.Formatter("[ROSE] %(levelname)s: %(message)s")
    console_handler.setFormatter(console_formatter)
    console_handler.addFilter(secret_filter)
    logger.addHandler(console_handler)

    # Prevent third-party HTTP clients from logging bot token URLs at INFO level
    for noisy_lib in ("httpx", "httpcore", "telegram", "urllib3"):
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)

    _CONFIGURED = True
    return logger


def get_logger(name: str = "rose") -> logging.Logger:
    if not _CONFIGURED:
        setup_logging()
    if name == "rose":
        return logging.getLogger("rose")
    return logging.getLogger(f"rose.{name}")

