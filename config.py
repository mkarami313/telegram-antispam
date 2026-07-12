"""Centralised configuration and logging setup.

All secrets and connection strings are read from environment variables so that
nothing sensitive is ever hard-coded into the source tree.

Environment variables
----------------------
MAIN_BOT_TOKEN   Telegram token of the manager ("factory") bot. Required by
                 ``antispambot.py``.
BOT_TOKEN        Telegram token of an individual anti-spam bot. Required by
                 ``child_bot.py`` (injected by the process manager).
OWNER_ID         Telegram user id that owns a given child bot. Required by
                 ``child_bot.py``.
DATABASE_URL     PostgreSQL DSN, e.g. ``postgresql://user:pass@host:5432/db``.
REDIS_URL        Redis URL, e.g. ``redis://localhost:6379/0``.
BOTS_LOG_DIR     Directory for per-child log files (default: ``logs``).
LOG_LEVEL        Root logging level (default: ``INFO``).
"""

from __future__ import annotations

import logging
import os

DEFAULT_DATABASE_URL = "postgresql://antispam:antispam@localhost:5432/antispam"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

DATABASE_URL = os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
REDIS_URL = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)

BOTS_LOG_DIR = os.environ.get("BOTS_LOG_DIR", "logs")

# TTL (seconds) after which a pending captcha challenge expires automatically.
PENDING_TTL_SECONDS = int(os.environ.get("PENDING_TTL_SECONDS", "3600"))
# TTL (seconds) for a manager conversation state ("waiting_token", ...).
STATE_TTL_SECONDS = int(os.environ.get("STATE_TTL_SECONDS", "1800"))

# Verification thresholds (kept in one place so UI copy and logic never drift).
TEMP_BAN_AT_FAILS = 3      # temporary 1h ban once this many wrong answers pile up
PERMANENT_BAN_AT_FAILS = 5  # permanent ban at/after this many wrong answers
TEMP_BAN_DURATION_SECONDS = 3600


def get_main_bot_token() -> str:
    """Return the manager bot token or raise if it is not configured."""
    token = os.environ.get("MAIN_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "MAIN_BOT_TOKEN is not set. Export it before starting the manager, "
            "e.g. `export MAIN_BOT_TOKEN=123456:ABC...`."
        )
    return token


_LOGGING_CONFIGURED = False


def setup_logging(name: str | None = None) -> logging.Logger:
    """Configure root logging once and return a named logger."""
    global _LOGGING_CONFIGURED
    if not _LOGGING_CONFIGURED:
        logging.basicConfig(
            level=os.environ.get("LOG_LEVEL", "INFO").upper(),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        _LOGGING_CONFIGURED = True
    return logging.getLogger(name if name else "antispam")
