"""
RSS feed fetching and parsing module.
Handles URL extraction from descriptions and retries on network errors.
"""

import logging
import re
import time
from html import unescape as html_unescape
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse, urlunparse

import feedparser
import requests

from src.config import Config

logger = logging.getLogger(__name__)

# Regex to extract any http/https URL from raw text (handles Sinhala + mixed content)
URL_PATTERN = re.compile(r'https?://[^\s\'"<>\]]+', re.UNICODE)

# Common image extensions used for thumbnail detection
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp")

# Vanity hosts that 301-redirect every path back to the site root.  Left alone,
# the bot would scrape and post the homepage instead of the article, and the
# "Read More" link would strand readers on the front page.
#   AdaDerana Sinhala (Aug 2026): rss.xml links to adaderanasinhala.lk/news/NNN,
#   which CloudFront redirects to https://sinhala.adaderana.lk/ — dropping the
#   path.  The canonical host serves the very same ID directly, so swap the host
#   and keep the path.
HOST_REWRITES = {
    "adaderanasinhala.lk": "sinhala.adaderana.lk",
    "www.adaderanasinhala.lk": "sinhala.adaderana.lk",
}


@dataclass
class ArticleItem:
    """Represents a single article parsed from an RSS feed."""
    title: str
    url: str
    description: str = ""
    image_url: Optional[str] = None
    published: str = ""
    source_name: str = ""
    feed_id: int = 0
    extra_urls: List[str] = field(default_factory=list)


def _extract_urls_from_text(text: str) -> List[str]:
    """Extract all HTTP/HTTPS URLs from a block of text (e.g. RSS description HTML)."""
    return URL_PATTERN.findall(text or "")


def _extract_image_from_entry(entry: feedparser.FeedParserDict) -> Optional[str]:
    """
    Try multiple feedparser fields to locate a thumbnail/image URL.
    Priority: media:content → enclosure → og:image in summary → None.
    """
    # media:content
    media = getattr(entry, "media_content", None)
    if media and isinstance(media, list):
        for m in media:
            url = m.get("url", "")
            if url and any(url.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
                return url

    # enclosure
    enclosures = getattr(entry, "enclosures", [])
    for enc in enclosures:
        url = enc.get("href", "")
        if url and "image" in enc.get("type", ""):
            return url
        if url and any(url.lower().endswith(ext) for ext in IMAGE_EXTENSIONS):
            return url

    # Fallback: any image URL in description HTML
    raw = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
    img_match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', raw, re.IGNORECASE)
    if img_match:
        return img_match.group(1)

    return None


def _rewrite_host(url: str) -> str:
    """
    Swap a known redirect-to-root vanity host for its canonical equivalent,
    preserving path and query. Unrecognised hosts are returned unchanged.
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
    except Exception:
        return url
    canonical = HOST_REWRITES.get(parsed.netloc.lower())
    if not canonical:
        return url
    rewritten = urlunparse(parsed._replace(scheme="https", netloc=canonical))
    logger.debug("Host rewritten: %s -> %s", url, rewritten)
    return rewritten


def _clean_url(url: str) -> str:
    """Strip trailing punctuation that regex sometimes captures."""
    return url.rstrip(".,;:)\"'")


def _is_valid_url(url: str) -> bool:
    """Basic sanity check for a URL."""
    try:
        result = urlparse(url)
        return all([result.scheme in ("http", "https"), result.netloc])
    except Exception:
        return False


def fetch_feed(
    feed_url: str,
    feed_id: int = 0,
    max_retries: int = None,
    timeout: int = None,
) -> List[ArticleItem]:
    """
    Fetch and parse an RSS/Atom feed, returning a list of ArticleItem objects.
    Retries on network errors with exponential back-off.

    Args:
        feed_url:    The RSS feed URL.
        feed_id:     Database ID of this feed (stored in each ArticleItem).
        max_retries: Override Config.MAX_RETRIES.
        timeout:     Override Config.REQUEST_TIMEOUT.

    Returns:
        List of ArticleItem (may be empty on failure).
    """
    max_retries = max_retries if max_retries is not None else Config.MAX_RETRIES
    timeout     = timeout     if timeout     is not None else Config.REQUEST_TIMEOUT

    parsed = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.debug("Fetching feed (attempt %d/%d): %s", attempt, max_retries, feed_url)
            # feedparser can download directly; pass etag/modified for caching later
            parsed = feedparser.parse(feed_url, request_headers={
                "User-Agent": "RSSNewsBot/1.0 (+https://github.com/your-org/rss-bot)"
            })
            if parsed.bozo and not parsed.entries:
                raise ValueError(f"Bozo feed: {parsed.bozo_exception}")
            break
        except Exception as exc:
            logger.warning("Feed fetch error (attempt %d): %s — %s", attempt, feed_url, exc)
            if attempt < max_retries:
                sleep_time = Config.RETRY_DELAY_SECONDS * attempt
                logger.debug("Retrying in %ds…", sleep_time)
                time.sleep(sleep_time)
            else:
                logger.error("All %d fetch attempts failed for %s", max_retries, feed_url)
                return []

    if not parsed or not parsed.entries:
        logger.info("No entries in feed: %s", feed_url)
        return []

    feed_title = parsed.feed.get("title", "Unknown Source")
    articles: List[ArticleItem] = []

    for entry in parsed.entries:
        # ── Resolve primary article URL ────────────────────────────────────────
        primary_url = _rewrite_host(entry.get("link", ""))

        # If the entry link points to a redirect or is missing, try extracting
        # from the raw description (common in AdaDerana-style feeds)
        description_raw = (
            getattr(entry, "summary", "")
            or getattr(entry, "description", "")
            or ""
        )
        extra_urls = [_rewrite_host(_clean_url(u)) for u in _extract_urls_from_text(description_raw)]
        extra_urls = [u for u in extra_urls if _is_valid_url(u) and u != primary_url]

        # Prefer first URL from description if primary is empty/invalid
        if not primary_url or not _is_valid_url(primary_url):
            if extra_urls:
                primary_url = extra_urls.pop(0)
            else:
                logger.debug("Skipping entry with no valid URL: %s", entry.get("title", "<no title>"))
                continue

        primary_url = _clean_url(primary_url)

        articles.append(ArticleItem(
            title=entry.get("title", "No Title").strip(),
            url=primary_url,
            # WordPress feeds (Iri News) double-encode their excerpt ellipsis,
            # so feedparser leaves a literal "&#8230;" behind — unescape it.
            description=html_unescape(re.sub(r"<[^>]+>", "", description_raw)).strip(),
            image_url=_extract_image_from_entry(entry),
            published=entry.get("published", ""),
            source_name=feed_title,
            feed_id=feed_id,
            extra_urls=extra_urls,
        ))

    logger.info("Fetched %d articles from %s", len(articles), feed_url)
    return articles
