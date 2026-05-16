"""
APScheduler integration for periodic RSS polling and database cleanup.
"""

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from src.config import Config
from src.database import Database

logger = logging.getLogger(__name__)


def create_scheduler(
    poll_callback,
    cleanup_callback,
    db: Database,
) -> AsyncIOScheduler:
    """
    Build and return a configured AsyncIOScheduler.

    Jobs:
        - poll_feeds:     every FETCH_INTERVAL_MINUTES
        - cleanup_db:     every 24 hours

    Args:
        poll_callback:    Async coroutine function — called each poll cycle.
        cleanup_callback: Async coroutine function — called for DB cleanup.
        db:               Database instance (used by cleanup job).

    Returns:
        Configured (but not yet started) AsyncIOScheduler.
    """
    scheduler = AsyncIOScheduler(timezone="UTC")

    # ── RSS poll job ──────────────────────────────────────────────────────────
    scheduler.add_job(
        poll_callback,
        trigger=IntervalTrigger(minutes=Config.FETCH_INTERVAL_MINUTES),
        id="poll_feeds",
        name="RSS Feed Poller",
        replace_existing=True,
        misfire_grace_time=60,  # seconds — tolerate short delays
    )
    logger.info(
        "Scheduled RSS poll every %d minutes.", Config.FETCH_INTERVAL_MINUTES
    )

    # ── DB cleanup job ────────────────────────────────────────────────────────
    scheduler.add_job(
        cleanup_callback,
        trigger=IntervalTrigger(hours=24),
        id="cleanup_db",
        name="Database Cleanup",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info("Scheduled DB cleanup every 24 hours.")

    return scheduler
