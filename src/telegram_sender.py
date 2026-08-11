"""
Telegram message formatter and sender.

Strategy: Always use send_message (4096 char limit).
Telegram automatically fetches the article's Open Graph image from the URL
and displays it as a link preview — no send_photo needed.
This avoids the 1024-char caption limit that cuts off long Sinhala articles.
"""

import asyncio
import logging
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError

from src.config import Config

logger = logging.getLogger(__name__)

# Telegram's hard limits
TELEGRAM_MESSAGE_LIMIT = 4096   # characters for send_message
# Reserve chars for the title line + footer line + formatting markup
_HEADER_RESERVE  = 300          # generous room for title + emoji
_FOOTER_RESERVE  = 150          # "🔗 Read More — Source" line
BODY_MAX_CHARS   = TELEGRAM_MESSAGE_LIMIT - _HEADER_RESERVE - _FOOTER_RESERVE  # ~3646


def _html_escape(text: str) -> str:
    """Minimal HTML escaping for Telegram HTML parse mode."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _truncate_body(text: str, limit: int = BODY_MAX_CHARS) -> str:
    """
    Truncate article body at a paragraph boundary so the message reads
    naturally.  Appends '…' if truncated.
    """
    if len(text) <= limit:
        return text
    chunk = text[:limit]
    # Prefer paragraph break
    para_break = chunk.rfind("\n\n")
    if para_break > limit // 2:
        return chunk[:para_break].strip() + "\n\n…"
    # Fall back to word boundary
    return chunk.rsplit(" ", 1)[0] + "…"


def _build_message(
    title: str,
    body: str,
    url: str,
    source_name: str,
) -> str:
    """
    Build the full Telegram HTML message.

    Format:
        📰 <b>Title</b>

        Body text (full article, up to ~3646 chars)…

        🔗 Read More — <a href="url">Source</a>

    Telegram will automatically show the article's OG image as a link
    preview from the URL, so no send_photo is required.
    """
    safe_title  = _html_escape(title)
    safe_body   = _html_escape(_truncate_body(body))
    safe_source = _html_escape(source_name or "Source")

    parts = [
        f"📰 <b>{safe_title}</b>",
        "",
        safe_body,
        "",
        f'🔗 <a href="{url}">Read More — {safe_source}</a>',
    ]
    return "\n".join(parts)


async def send_article(
    bot: Bot,
    chat_id: str,
    title: str,
    summary: str,
    url: str,
    source_name: str = "",
    image_url: Optional[str] = None,   # kept for API compatibility; not used directly
    max_retries: int = None,
) -> bool:
    """
    Send a formatted article to a Telegram chat.

    Always uses send_message with disable_web_page_preview=False so Telegram
    auto-fetches the article's OG thumbnail.  This gives the full 4096-char
    limit instead of the 1024-char photo caption limit.

    Args:
        bot:         python-telegram-bot Bot instance.
        chat_id:     Target channel/group ID or @username.
        title:       Article headline.
        summary:     Article body text (may be up to MAX_SUMMARY_LENGTH chars).
        url:         Article URL — triggers Telegram link preview with image.
        source_name: Feed source name shown in the footer link.
        image_url:   Ignored (Telegram fetches image from the URL itself).
        max_retries: Override Config.MAX_RETRIES.

    Returns:
        True on success, False after all retries exhausted.
    """
    max_retries = max_retries if max_retries is not None else Config.MAX_RETRIES
    message_text = _build_message(title, summary, url, source_name)

    for attempt in range(1, max_retries + 1):
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,  # shows OG image preview from URL
            )
            logger.info("Sent article to %s: %s", chat_id, title[:60])
            return True

        except RetryAfter as exc:
            wait = exc.retry_after + 1
            logger.warning("Flood control — waiting %ds (attempt %d)", wait, attempt)
            await asyncio.sleep(wait)

        except (BadRequest, Forbidden) as exc:
            # Permanent — the chat is wrong or the bot has no access there.
            # Retrying cannot fix it, so fail fast with an actionable message.
            logger.error(
                "Cannot post to chat %s: %s. The feed's stored channel_id is likely "
                "stale (it is set from CHANNEL_ID when the feed is added, not at send "
                "time) or the bot is not an admin of that channel.",
                chat_id, exc,
            )
            return False

        except TelegramError as exc:
            logger.error(
                "TelegramError sending article (attempt %d/%d): %s", attempt, max_retries, exc
            )
            if attempt < max_retries:
                await asyncio.sleep(Config.RETRY_DELAY_SECONDS * attempt)

        except Exception as exc:
            logger.error(
                "Unexpected error sending article (attempt %d/%d): %s", attempt, max_retries, exc
            )
            if attempt < max_retries:
                await asyncio.sleep(Config.RETRY_DELAY_SECONDS * attempt)

    logger.error("Failed to send article after %d attempts: %s", max_retries, title[:60])
    return False


async def send_text(
    bot: Bot,
    chat_id: str,
    text: str,
    parse_mode: str = ParseMode.HTML,
) -> None:
    """Utility wrapper for sending plain / HTML text messages."""
    try:
        if len(text) > TELEGRAM_MESSAGE_LIMIT:
            text = text[:TELEGRAM_MESSAGE_LIMIT - 1] + "…"
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
        )
    except TelegramError as exc:
        logger.error("Failed to send text to %s: %s", chat_id, exc)
