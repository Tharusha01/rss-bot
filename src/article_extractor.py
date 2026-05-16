"""
Article content extraction module.
Fetches full webpage HTML and extracts clean, readable text.
Supports Sinhala Unicode and falls back gracefully on parse failures.
"""

import logging
import re
import time
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.config import Config

logger = logging.getLogger(__name__)

# Request headers to mimic a real browser (reduces bot-detection blocks)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,si;q=0.8",  # Sinhala locale hint
    "Accept-Encoding": "gzip, deflate, br",
}

# Tags that typically hold the main article body (priority order)
ARTICLE_SELECTORS = [
    "article",
    '[role="main"]',
    ".article-body",
    ".post-content",
    ".entry-content",
    ".td-post-content",
    ".story-body",
    "#article-body",
    "#main-content",
    "main",
]

# Tags to strip from the extracted content
NOISE_TAGS = [
    "script", "style", "nav", "header", "footer",
    "aside", "form", "button", "iframe", "noscript",
    "figcaption", "figure",
]


def _fetch_html(url: str, timeout: int, max_retries: int) -> Optional[str]:
    """
    Download the HTML for a given URL with retry and exponential back-off.

    Returns:
        HTML string on success, None on failure.
    """
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(
                url,
                headers=DEFAULT_HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
            response.raise_for_status()
            # Force UTF-8 to handle Sinhala characters correctly
            response.encoding = response.apparent_encoding or "utf-8"
            return response.text
        except requests.RequestException as exc:
            logger.warning("HTML fetch error (attempt %d/%d): %s — %s", attempt, max_retries, url, exc)
            if attempt < max_retries:
                time.sleep(Config.RETRY_DELAY_SECONDS * attempt)
    return None


def _extract_og_image(soup: BeautifulSoup) -> Optional[str]:
    """Pull Open Graph or Twitter card image from <meta> tags."""
    for prop in ("og:image", "twitter:image"):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def _extract_body_text(soup: BeautifulSoup) -> str:
    """
    Extract the main body text from the parsed HTML.
    Tries known article selectors before falling back to <body>.
    """
    # Remove noisy elements first
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()

    # Try specific selectors
    for selector in ARTICLE_SELECTORS:
        container = soup.select_one(selector)
        if container:
            return container.get_text(separator="\n", strip=True)

    # Generic fallback: body text
    body = soup.find("body")
    if body:
        return body.get_text(separator="\n", strip=True)

    return soup.get_text(separator="\n", strip=True)


def _clean_text(raw: str) -> str:
    """
    Normalise whitespace while preserving Sinhala Unicode characters.
    Collapses multiple blank lines and trims leading/trailing spaces.
    """
    # Collapse consecutive blank lines
    text = re.sub(r"\n{3,}", "\n\n", raw)
    # Collapse multiple spaces (but not across newlines)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _summarise(text: str, max_length: int) -> str:
    """
    Return the first *max_length* characters of *text*, ending at a word boundary.
    Appends '…' if truncated.
    """
    if len(text) <= max_length:
        return text
    truncated = text[:max_length].rsplit(" ", 1)[0]
    return truncated + "…"


def extract_article(
    url: str,
    fallback_description: str = "",
    max_retries: int = None,
    timeout: int = None,
    max_summary_length: int = None,
) -> Tuple[str, Optional[str]]:
    """
    Fetch and extract article text and image from a URL.

    Tries newspaper3k first (best quality), then falls back to
    BeautifulSoup extraction, then uses the RSS description.

    Args:
        url:                  Article URL to fetch.
        fallback_description: RSS item description used as last resort.
        max_retries:          Override Config.MAX_RETRIES.
        timeout:              Override Config.REQUEST_TIMEOUT.
        max_summary_length:   Override Config.MAX_SUMMARY_LENGTH.

    Returns:
        (summary_text, image_url)  — image_url may be None.
    """
    max_retries        = max_retries        if max_retries        is not None else Config.MAX_RETRIES
    timeout            = timeout            if timeout            is not None else Config.REQUEST_TIMEOUT
    max_summary_length = max_summary_length if max_summary_length is not None else Config.MAX_SUMMARY_LENGTH

    image_url: Optional[str] = None
    body_text: str = ""

    # ── Strategy 1: newspaper3k ──────────────────────────────────────────────
    try:
        from newspaper import Article  # type: ignore
        article = Article(url, language="si")  # 'si' = Sinhala; falls back to English
        article.download()
        article.parse()
        body_text = article.text or ""
        if article.top_image:
            image_url = article.top_image
        logger.debug("newspaper3k extracted %d chars from %s", len(body_text), url)
    except Exception as exc:
        logger.debug("newspaper3k failed for %s: %s — falling back to BeautifulSoup", url, exc)

    # ── Strategy 2: BeautifulSoup ────────────────────────────────────────────
    if not body_text:
        html = _fetch_html(url, timeout=timeout, max_retries=max_retries)
        if html:
            try:
                soup = BeautifulSoup(html, "lxml")
                if not image_url:
                    image_url = _extract_og_image(soup)
                body_text = _extract_body_text(soup)
                logger.debug("BeautifulSoup extracted %d chars from %s", len(body_text), url)
            except Exception as exc:
                logger.warning("BeautifulSoup parse error for %s: %s", url, exc)

    # ── Strategy 3: RSS description fallback ────────────────────────────────
    if not body_text:
        body_text = re.sub(r"<[^>]+>", "", fallback_description).strip()
        logger.info("Using RSS description fallback for %s", url)

    body_text = _clean_text(body_text)
    summary   = _summarise(body_text, max_summary_length)

    return summary, image_url
