"""
Configuration module for the RSS News Bot.
Loads and validates all environment variables.
"""

import os
import logging
from typing import List, Optional
from dotenv import load_dotenv

# Load .env file
load_dotenv()

logger = logging.getLogger(__name__)


class Config:
    """Central configuration class loaded from environment variables."""

    # ── Telegram ──────────────────────────────────────────────────────────────
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    CHANNEL_ID: str = os.getenv("CHANNEL_ID", "")  # e.g. @mychannel or -100xxx
    ADMIN_IDS: List[int] = [
        int(uid.strip())
        for uid in os.getenv("ADMIN_IDS", "").split(",")
        if uid.strip().isdigit()
    ]

    # ── Scheduler ─────────────────────────────────────────────────────────────
    FETCH_INTERVAL_MINUTES: int = int(os.getenv("FETCH_INTERVAL_MINUTES", "5"))

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/rss_bot.db")

    # ── Article fetch settings ────────────────────────────────────────────────
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "15"))
    MAX_SUMMARY_LENGTH: int = int(os.getenv("MAX_SUMMARY_LENGTH", "3600"))
    MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "3"))
    RETRY_DELAY_SECONDS: int = int(os.getenv("RETRY_DELAY_SECONDS", "5"))

    # ── Rate limiting ─────────────────────────────────────────────────────────
    MESSAGE_DELAY_SECONDS: float = float(os.getenv("MESSAGE_DELAY_SECONDS", "1.5"))
    MAX_MESSAGES_PER_FEED: int = int(os.getenv("MAX_MESSAGES_PER_FEED", "5"))

    # ── Cleanup ───────────────────────────────────────────────────────────────
    # Days to keep sent-article records before pruning
    DB_CLEANUP_DAYS: int = int(os.getenv("DB_CLEANUP_DAYS", "30"))

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
    LOG_FILE: Optional[str] = os.getenv("LOG_FILE", "logs/rss_bot.log")

    @classmethod
    def validate(cls) -> None:
        """Raise ValueError if required configuration is missing."""
        errors: List[str] = []
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN is required")
        if not cls.CHANNEL_ID:
            errors.append("CHANNEL_ID is required (e.g. @mychannel or -1001234567890)")
        if errors:
            raise ValueError("Configuration errors:\n" + "\n".join(f"  • {e}" for e in errors))
        logger.info("Configuration validated successfully.")
