"""
RSS News Bot — Main entry point.
Wires together the database, scheduler, and Telegram bot.
"""

import asyncio
import logging
import sys

from telegram.ext import Application, CommandHandler

from src.config import Config
from src.database import Database
from src.handlers import (
    cmd_add_feed,
    cmd_latest,
    cmd_list_feeds,
    cmd_remove_feed,
    cmd_start,
    cmd_test_feed,
)
from src.logger import setup_logging
from src.poller import cleanup_db, poll_all_feeds
from src.scheduler import create_scheduler

logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    """
    Called once by python-telegram-bot after the Application initialises.
    - Starts the APScheduler.
    - Registers shared objects in bot_data so handlers can access them.
    """
    db: Database = application.bot_data["db"]
    bot = application.bot

    # Wrap poller and cleanup into zero-arg coroutines for the scheduler
    async def _poll():
        await poll_all_feeds(bot=bot, db=db)

    async def _cleanup():
        await cleanup_db(db=db)

    # Store poll function reference so /latest can call it directly
    application.bot_data["poll_fn"] = _poll

    scheduler = create_scheduler(
        poll_callback=_poll,
        cleanup_callback=_cleanup,
        db=db,
    )
    application.bot_data["scheduler"] = scheduler
    scheduler.start()
    logger.info("Scheduler started.")


async def post_shutdown(application: Application) -> None:
    """Gracefully shut down the scheduler on bot exit."""
    scheduler = application.bot_data.get("scheduler")
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")


def main() -> None:
    # ── 1. Logging ────────────────────────────────────────────────────────────
    setup_logging(log_level=Config.LOG_LEVEL, log_file=Config.LOG_FILE)

    # ── 2. Config validation ──────────────────────────────────────────────────
    try:
        Config.validate()
    except ValueError as exc:
        logger.critical("Invalid configuration:\n%s", exc)
        sys.exit(1)

    # ── 3. Database ───────────────────────────────────────────────────────────
    db = Database(Config.DATABASE_PATH)

    # ── 4. Build Application ──────────────────────────────────────────────────
    app = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Store DB reference so handlers can access it via context.bot_data
    app.bot_data["db"] = db

    # ── 5. Register command handlers ──────────────────────────────────────────
    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("addfeed",    cmd_add_feed))
    app.add_handler(CommandHandler("removefeed", cmd_remove_feed))
    app.add_handler(CommandHandler("listfeeds",  cmd_list_feeds))
    app.add_handler(CommandHandler("testfeed",   cmd_test_feed))
    app.add_handler(CommandHandler("latest",     cmd_latest))

    logger.info("Starting RSS News Bot…")

    # ── 6. Run (blocks until SIGINT / SIGTERM) ────────────────────────────────
    app.run_polling(
        allowed_updates=["message"],
        drop_pending_updates=True,   # ignore messages sent while bot was offline
    )


if __name__ == "__main__":
    main()
