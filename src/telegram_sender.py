"""
Telegram message formatter and sender.
Handles rate-limiting, image attachments, HTML escaping, and retries.
"""

import asyncio
import logging
from typing import Optional

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import RetryAfter, TelegramError
from telegram.helpers import escape_markdown

from src.config import Config

logger = logging.getLogger(__name__)

# Maximum caption length allowed by Telegram for photo messages
TELEGRAM_CAPTION_LIMIT = 1024
# Maximum text length for a sendMessage call
TELEGRAM_MESSAGE_LIMIT = 4096


def _build_message_text(
    title: str,
    summary: str,
    url: str,
    source_name: str,
) -> str:
    """
    Compose the Telegram message using HTML parse mode.
    Handles Sinhala Unicode (Telegram supports it natively in HTML mode).

    Format:
        📰 <b>Title</b>

        Summary text…

        🔗 Read More: <a href="url">source</a>
    """
    # Escape HTML special characters in user-controlled content
    safe_title   = _html_escape(title)
    safe_summary = _html_escape(summary)
    safe_source  = _html_escape(source_name or "Source")

    lines = [
        f"📰 <b>{safe_title}</b>",
        "",
        safe_summary,
        "",
        f'🔗 <a href="{url}">Read More — {safe_source}</a>',
    ]
    return "\n".join(lines)


def _html_escape(text: str) -> str:
    """Minimal HTML escaping for Telegram HTML parse mode."""
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


def _truncate(text: str, limit: int) -> str:
    """Truncate text to fit within Telegram limits."""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


async def send_article(
    bot: Bot,
    chat_id: str,
    title: str,
    summary: str,
    url: str,
    source_name: str = "",
    image_url: Optional[str] = None,
    max_retries: int = None,
) -> bool:
    """
    Send a formatted article message to a Telegram chat.

    Tries to send with a photo first; falls back to plain text on failure.
    Handles Telegram RetryAfter (flood control) automatically.

    Args:
        bot:         python-telegram-bot Bot instance.
        chat_id:     Target channel/group ID or username.
        title:       Article headline.
        summary:     Short article summary.
        url:         Article URL (used as "Read More" link).
        source_name: Human-readable feed source name.
        image_url:   Optional thumbnail URL.
        max_retries: Override Config.MAX_RETRIES.

    Returns:
        True on success, False on failure.
    """
    max_retries = max_retries if max_retries is not None else Config.MAX_RETRIES
    message_text = _build_message_text(title, summary, url, source_name)

    for attempt in range(1, max_retries + 1):
        try:
            if image_url:
                caption = _truncate(message_text, TELEGRAM_CAPTION_LIMIT)
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=image_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
            else:
                text = _truncate(message_text, TELEGRAM_MESSAGE_LIMIT)
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=False,
                )
            logger.info("Sent article to %s: %s", chat_id, title[:60])
            return True

        except RetryAfter as exc:
            # Telegram flood control — wait the required seconds
            wait = exc.retry_after + 1
            logger.warning("Flood control hit; waiting %ds (attempt %d)", wait, attempt)
            await asyncio.sleep(wait)

        except TelegramError as exc:
            if image_url and ("wrong file identifier" in str(exc).lower()
                              or "invalid url" in str(exc).lower()):
                # Image URL bad — retry without the image
                logger.warning("Bad image URL; retrying without image: %s", image_url)
                image_url = None
                continue
            logger.error("TelegramError sending article (attempt %d): %s", attempt, exc)
            if attempt < max_retries:
                await asyncio.sleep(Config.RETRY_DELAY_SECONDS * attempt)

        except Exception as exc:
            logger.error("Unexpected error sending article (attempt %d): %s", attempt, exc)
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
    """Utility wrapper for sending plain/HTML text messages."""
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=_truncate(text, TELEGRAM_MESSAGE_LIMIT),
            parse_mode=parse_mode,
        )
    except TelegramError as exc:
        logger.error("Failed to send text to %s: %s", chat_id, exc)
