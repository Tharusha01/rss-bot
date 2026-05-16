"""
Telegram bot command handlers.
All feed-management commands are admin-only.
"""

import asyncio
import logging
from typing import Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from src.article_extractor import extract_article
from src.config import Config
from src.database import Database
from src.feed_parser import fetch_feed
from src.telegram_sender import send_article, send_text

logger = logging.getLogger(__name__)


# ── Guard decorator ────────────────────────────────────────────────────────────

def admin_only(handler):
    """Decorator: silently ignore commands from non-admin users."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id not in Config.ADMIN_IDS:
            logger.warning(
                "Rejected command from non-admin user %s (%s)",
                user.username if user else "unknown",
                user.id if user else "?",
            )
            await update.message.reply_text(
                "⛔ You are not authorised to use this command."
            )
            return
        return await handler(update, context)
    wrapper.__name__ = handler.__name__
    return wrapper


# ── /start ─────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a welcome message with available commands."""
    text = (
        "👋 <b>RSS News Bot</b> is running!\n\n"
        "📋 <b>Available Commands:</b>\n"
        "  /addfeed &lt;url&gt;    — Add an RSS feed\n"
        "  /removefeed &lt;url&gt; — Remove an RSS feed\n"
        "  /listfeeds          — List all active feeds\n"
        "  /testfeed &lt;url&gt;  — Test an RSS feed\n"
        "  /latest             — Fetch latest articles now\n\n"
        "ℹ️ Feed management commands are admin-only."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /addfeed ───────────────────────────────────────────────────────────────────

@admin_only
async def cmd_add_feed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /addfeed <rss_url>
    Adds an RSS feed for the configured channel.
    """
    db: Database = context.bot_data["db"]

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: /addfeed &lt;rss_url&gt;",
            parse_mode=ParseMode.HTML,
        )
        return

    url = context.args[0].strip()
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("⚠️ Please provide a valid HTTP/HTTPS URL.")
        return

    # Quick validation: try to fetch a couple of entries
    await update.message.reply_text(f"🔍 Validating feed: <code>{url}</code>…", parse_mode=ParseMode.HTML)
    articles = fetch_feed(url, max_retries=1)
    if not articles:
        await update.message.reply_text(
            "❌ Could not fetch entries from that feed. Please check the URL."
        )
        return

    added = db.add_feed(url, added_by=update.effective_user.id, channel_id=Config.CHANNEL_ID)
    if added:
        await update.message.reply_text(
            f"✅ Feed added successfully!\n"
            f"📡 <code>{url}</code>\n"
            f"Found {len(articles)} article(s).",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("ℹ️ That feed is already active.")


# ── /removefeed ────────────────────────────────────────────────────────────────

@admin_only
async def cmd_remove_feed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/removefeed <rss_url> — Deactivate an RSS feed."""
    db: Database = context.bot_data["db"]

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: /removefeed &lt;rss_url&gt;",
            parse_mode=ParseMode.HTML,
        )
        return

    url = context.args[0].strip()
    removed = db.remove_feed(url)
    if removed:
        await update.message.reply_text(
            f"🗑 Feed removed:\n<code>{url}</code>",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text("⚠️ No active feed found with that URL.")


# ── /listfeeds ─────────────────────────────────────────────────────────────────

@admin_only
async def cmd_list_feeds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/listfeeds — Show all active RSS feeds."""
    db: Database = context.bot_data["db"]
    feeds = db.get_active_feeds()

    if not feeds:
        await update.message.reply_text("📭 No active feeds configured.")
        return

    lines = ["📋 <b>Active RSS Feeds:</b>\n"]
    for i, feed in enumerate(feeds, 1):
        lines.append(f"{i}. <code>{feed['url']}</code>")
        lines.append(f"   Channel: {feed['channel_id']} | Added: {feed['created_at'][:10]}")

    stats = db.get_stats()
    lines.append(f"\n📊 {stats['active_feeds']} feeds · {stats['sent_articles']} articles sent")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


# ── /testfeed ──────────────────────────────────────────────────────────────────

@admin_only
async def cmd_test_feed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/testfeed <rss_url> — Fetch the latest article from a feed and preview it."""
    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: /testfeed &lt;rss_url&gt;",
            parse_mode=ParseMode.HTML,
        )
        return

    url = context.args[0].strip()
    await update.message.reply_text(f"⏳ Testing feed: <code>{url}</code>…", parse_mode=ParseMode.HTML)

    articles = fetch_feed(url, max_retries=1)
    if not articles:
        await update.message.reply_text("❌ No articles found. Check the URL or feed format.")
        return

    # Take the first article and extract content
    item = articles[0]
    summary, image_url = extract_article(
        item.url,
        fallback_description=item.description,
        max_retries=1,
    )

    success = await send_article(
        bot=context.bot,
        chat_id=update.effective_chat.id,
        title=item.title,
        summary=summary or item.description[:300],
        url=item.url,
        source_name=item.source_name,
        image_url=image_url or item.image_url,
        max_retries=1,
    )

    if not success:
        await update.message.reply_text("⚠️ Test message failed to send. Check logs.")
    else:
        await update.message.reply_text(
            f"✅ Test complete — {len(articles)} article(s) in feed."
        )


# ── /latest ────────────────────────────────────────────────────────────────────

@admin_only
async def cmd_latest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/latest — Trigger an immediate RSS poll for all feeds."""
    poll_fn = context.bot_data.get("poll_fn")
    if not poll_fn:
        await update.message.reply_text("⚠️ Poller not initialised.")
        return

    await update.message.reply_text("🔄 Triggering immediate feed poll…")
    try:
        await poll_fn()
        await update.message.reply_text("✅ Poll complete. Check the channel for new articles.")
    except Exception as exc:
        logger.error("Manual poll error: %s", exc)
        await update.message.reply_text(f"❌ Poll failed: {exc}")
