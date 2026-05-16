"""
Core RSS polling logic — the heart of the bot's background task.
Called by the scheduler and /latest command.
"""

import asyncio
import logging
from typing import Optional

from telegram import Bot

from src.article_extractor import extract_article
from src.config import Config
from src.database import Database
from src.feed_parser import fetch_feed
from src.telegram_sender import send_article

logger = logging.getLogger(__name__)


async def poll_all_feeds(bot: Bot, db: Database) -> None:
    """
    Iterate over all active feeds, fetch new articles, and send them to Telegram.

    - Skips articles already in sent_articles (deduplication).
    - Respects MAX_MESSAGES_PER_FEED to prevent bursting.
    - Adds MESSAGE_DELAY_SECONDS between sends for rate limiting.
    """
    feeds = db.get_active_feeds()
    if not feeds:
        logger.info("No active feeds to poll.")
        return

    logger.info("Polling %d feed(s)…", len(feeds))

    for feed in feeds:
        feed_id    = feed["id"]
        feed_url   = feed["url"]
        channel_id = feed["channel_id"]

        logger.debug("Checking feed [%d]: %s", feed_id, feed_url)

        try:
            articles = fetch_feed(feed_url, feed_id=feed_id)
        except Exception as exc:
            logger.error("Error fetching feed [%d] %s: %s", feed_id, feed_url, exc)
            continue

        sent_count = 0
        for item in articles:
            if sent_count >= Config.MAX_MESSAGES_PER_FEED:
                logger.debug("Max messages/feed reached for %s", feed_url)
                break

            # ── Deduplication check ────────────────────────────────────────
            if db.is_article_sent(feed_id, item.url):
                logger.debug("Skipping already-sent article: %s", item.url)
                continue

            # ── Extract full article content ───────────────────────────────
            try:
                summary, image_url = extract_article(
                    item.url,
                    fallback_description=item.description,
                    page_title=item.title,
                )
            except Exception as exc:
                logger.warning("Extraction failed for %s: %s", item.url, exc)
                summary   = item.description[:Config.MAX_SUMMARY_LENGTH] or "No summary available."
                image_url = item.image_url

            effective_image = image_url or item.image_url

            # ── Send to Telegram ───────────────────────────────────────────
            success = await send_article(
                bot=bot,
                chat_id=channel_id,
                title=item.title,
                summary=summary or "No summary available.",
                url=item.url,
                source_name=item.source_name,
                image_url=effective_image,
            )

            if success:
                db.mark_article_sent(feed_id, item.url)
                sent_count += 1
                logger.info(
                    "Posted [feed %d] to %s: %s", feed_id, channel_id, item.title[:60]
                )
                # Rate-limit delay between messages
                await asyncio.sleep(Config.MESSAGE_DELAY_SECONDS)

    logger.info("Poll cycle complete.")


async def cleanup_db(db: Database) -> None:
    """Delete old sent_articles records according to DB_CLEANUP_DAYS."""
    deleted = db.cleanup_old_records(days=Config.DB_CLEANUP_DAYS)
    logger.info("DB cleanup removed %d old record(s).", deleted)
