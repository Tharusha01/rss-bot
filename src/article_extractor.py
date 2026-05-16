"""
Article content extraction module.

Extraction priority (always fetches the real webpage first):
  1. Site-specific selectors  — div.news-content and other known Sri Lankan news layouts
  2. readability-lxml          — Mozilla algorithm; auto-removes ads/nav
  3. newspaper3k               — NLP-based fallback
  4. BeautifulSoup generic     — Generic body text extraction
  5. RSS description           — ABSOLUTE LAST RESORT only

Sinhala Unicode is fully preserved (UTF-8 enforced at every stage).
"""

import logging
import re
import time
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from src.config import Config

logger = logging.getLogger(__name__)

# ── HTTP request headers ───────────────────────────────────────────────────────
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "si-LK,si;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
}

# ── Site-specific CSS selectors (tried first, in order) ───────────────────────
# Add new selectors here when supporting additional news sites.
SITE_SPECIFIC_SELECTORS: List[str] = [
    # AdaDerana Sinhala / English
    "div.news-content",
    "div.article-body",
    "div.news-article-body",
    # Hiru News
    "div.single-blog-post",
    "div.post-inner-content",
    # Derana / ITN
    "div.full-article",
    "div.entry-content",
    # Lankadeepa / Divaina
    "div.article-text",
    "div.field-items",
    "div.field-item",
    # Generic article containers
    "article .content",
    "article .body",
    "article",
    '[itemprop="articleBody"]',
    ".story-body",
    ".post-content",
    ".td-post-content",
    ".mvp-content-main",
    "#article-content",
    "#main-content",
    "main",
]

# ── Noise patterns to remove before extraction ────────────────────────────────
_NOISE_TAG_NAMES = [
    "script", "style", "nav", "header", "footer",
    "aside", "form", "button", "iframe", "noscript", "ins",
]

_NOISE_CLASS_PATTERN = re.compile(
    r"(advert|sponsor|related|social|share|comment|sidebar|widget"
    r"|breadcrumb|tag-cloud|newsletter|popup|banner|promo"
    r"|more-news|trending|latest-news-widget|read-also)",
    re.IGNORECASE,
)


# ── Shared HTML downloader ────────────────────────────────────────────────────

def _fetch_html(url: str, timeout: int, max_retries: int) -> Optional[str]:
    """
    Download raw HTML for *url* with retry + exponential back-off.
    Forces UTF-8 decoding so Sinhala characters are never mangled.
    """
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(
                url,
                headers=DEFAULT_HEADERS,
                timeout=timeout,
                allow_redirects=True,
            )
            resp.raise_for_status()

            # Determine correct encoding for Sinhala pages
            content_type = resp.headers.get("Content-Type", "").lower()
            if "charset=utf-8" in content_type or "charset=utf8" in content_type:
                resp.encoding = "utf-8"
            else:
                # apparent_encoding uses chardet — reliable for Sinhala
                detected = resp.apparent_encoding or "utf-8"
                resp.encoding = detected

            return resp.text

        except requests.RequestException as exc:
            logger.warning(
                "HTML fetch attempt %d/%d failed for %s: %s",
                attempt, max_retries, url, exc,
            )
            if attempt < max_retries:
                time.sleep(Config.RETRY_DELAY_SECONDS * attempt)

    logger.error("All %d fetch attempts failed for %s", max_retries, url)
    return None


# ── Noise removal ─────────────────────────────────────────────────────────────

def _strip_noise(soup: BeautifulSoup) -> None:
    """
    Remove ads, navigation, sidebars, related-article widgets etc. in-place.
    Works on both tag names and class/id attribute patterns.
    """
    # Remove by tag name
    for tag in soup.find_all(_NOISE_TAG_NAMES):
        tag.decompose()

    # Remove by CSS class / id patterns
    for tag in soup.find_all(True):
        classes = " ".join(tag.get("class", []))
        tag_id  = tag.get("id", "")
        if _NOISE_CLASS_PATTERN.search(classes) or _NOISE_CLASS_PATTERN.search(tag_id):
            tag.decompose()


# ── Helper: extract og:image ──────────────────────────────────────────────────

def _og_image(soup: BeautifulSoup) -> Optional[str]:
    """Pull Open Graph or Twitter card image from <meta> tags."""
    for prop in ("og:image", "twitter:image"):
        tag = (
            soup.find("meta", property=prop)
            or soup.find("meta", attrs={"name": prop})
        )
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


# ── Strategy 1: site-specific CSS selectors ───────────────────────────────────

def _extract_via_selectors(
    html: str,
    url: str,
) -> Tuple[str, Optional[str]]:
    """
    Try each selector in SITE_SPECIFIC_SELECTORS in order.
    Returns the text of the first container that yields ≥ 150 characters.

    This is the PRIMARY strategy and covers all known Sri Lankan news sites.

    Returns:
        (body_text, image_url_or_None)
    """
    try:
        soup = BeautifulSoup(html, "html.parser")  # pure-Python, handles malformed HTML
        image_url = _og_image(soup)

        for selector in SITE_SPECIFIC_SELECTORS:
            container = soup.select_one(selector)
            if not container:
                continue

            # Work on a copy so we don't corrupt the original soup
            container_copy = BeautifulSoup(str(container), "html.parser")
            _strip_noise(container_copy)

            # Preserve paragraphs — join <p> tags with double newline
            paragraphs: List[str] = []
            for elem in container_copy.find_all(["p", "h2", "h3", "h4", "li"]):
                text = elem.get_text(strip=True)
                if text:
                    paragraphs.append(text)

            if paragraphs:
                body = "\n\n".join(paragraphs)
            else:
                body = container_copy.get_text(separator="\n", strip=True)

            if len(body.strip()) >= 150:
                logger.info(
                    "Selector '%s' extracted %d chars from %s",
                    selector, len(body), url,
                )
                return body, image_url

    except Exception as exc:
        logger.warning("Selector extraction failed for %s: %s", url, exc)

    return "", None


# ── Strategy 2: readability-lxml ──────────────────────────────────────────────

def _extract_via_readability(html: str, url: str) -> Tuple[str, Optional[str]]:
    """
    Mozilla Readability algorithm — excellent for removing ads and boilerplate
    from pages where the article container class is unknown.
    """
    try:
        from readability import Document  # type: ignore

        doc          = Document(html)
        article_html = doc.summary(html_partial=True)

        soup = BeautifulSoup(article_html, "html.parser")
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
        body = "\n\n".join(paragraphs) if paragraphs else soup.get_text(separator="\n", strip=True)

        if len(body.strip()) >= 150:
            logger.info("readability-lxml extracted %d chars from %s", len(body), url)
            return body, None

    except Exception as exc:
        logger.debug("readability-lxml failed for %s: %s", url, exc)

    return "", None


# ── Strategy 3: newspaper3k ───────────────────────────────────────────────────

def _extract_via_newspaper(url: str, html: str) -> Tuple[str, Optional[str]]:
    """
    newspaper3k — uses NLP to detect the article block.
    Passes the already-downloaded HTML to avoid a second request.
    """
    try:
        from newspaper import Article, Config as NConfig  # type: ignore

        ncfg = NConfig()
        ncfg.browser_user_agent = DEFAULT_HEADERS["User-Agent"]
        ncfg.fetch_images = True
        ncfg.memoize_articles = False

        article = Article(url, config=ncfg, language="si")
        article.set_html(html)
        article.parse()

        body  = article.text or ""
        image = article.top_image or None

        if len(body.strip()) >= 150:
            logger.info("newspaper3k extracted %d chars from %s", len(body), url)
            return body, image

    except Exception as exc:
        logger.debug("newspaper3k failed for %s: %s", url, exc)

    return "", None


# ── Strategy 4: generic BS4 body text ────────────────────────────────────────

def _extract_via_body(html: str, url: str) -> Tuple[str, Optional[str]]:
    """Last BeautifulSoup attempt — strips all noise and returns full <body> text."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        image_url = _og_image(soup)
        _strip_noise(soup)
        body = soup.find("body")
        if body:
            text = body.get_text(separator="\n", strip=True)
            if len(text.strip()) >= 150:
                logger.info("Generic body extraction got %d chars from %s", len(text), url)
                return text, image_url
    except Exception as exc:
        logger.debug("Generic body extraction failed for %s: %s", url, exc)
    return "", None


# ── Text post-processing ──────────────────────────────────────────────────────

def _clean_text(raw: str) -> str:
    """Normalise whitespace while preserving Sinhala Unicode."""
    text = re.sub(r"\n{3,}", "\n\n", raw)      # max 2 blank lines
    text = re.sub(r"[ \t]{2,}", " ", text)     # collapse spaces
    return text.strip()


def _remove_duplicate_title(text: str, title: str) -> str:
    """Strip headline if it appears verbatim at the very top of the body."""
    if not title:
        return text
    stripped = text.strip()
    if stripped.lower().startswith(title.lower()):
        return stripped[len(title):].lstrip("\n: ").strip()
    return stripped


def _truncate_for_telegram(text: str, max_length: int) -> str:
    """
    Truncate at a paragraph boundary so the message reads naturally.
    Appends '…' if truncated.
    """
    if len(text) <= max_length:
        return text
    # Try to cut at a paragraph boundary
    chunk = text[:max_length]
    para_break = chunk.rfind("\n\n")
    if para_break > max_length // 2:
        return chunk[:para_break].strip() + "\n\n…"
    # Fall back to word boundary
    return chunk.rsplit(" ", 1)[0] + "…"


# ── Public API ────────────────────────────────────────────────────────────────

def extract_article(
    url: str,
    fallback_description: str = "",
    page_title: str = "",
    max_retries: int = None,
    timeout: int = None,
    max_summary_length: int = None,
) -> Tuple[str, Optional[str]]:
    """
    Fetch the FULL article content from *url*.

    Always downloads the real webpage — never uses the RSS description
    as the primary content source.

    Extraction order:
      1.  Site-specific CSS selectors (div.news-content etc.)
      2.  readability-lxml
      3.  newspaper3k
      4.  Generic <body> text
      5.  RSS fallback_description  ← only if the webpage cannot be reached

    Args:
        url:                  Article URL to scrape.
        fallback_description: Raw RSS item description (last resort only).
        page_title:           Headline — used to remove duplicate title from body.
        max_retries:          Override Config.MAX_RETRIES.
        timeout:              Override Config.REQUEST_TIMEOUT.
        max_summary_length:   Override Config.MAX_SUMMARY_LENGTH.

    Returns:
        (full_text_truncated_for_telegram, image_url_or_None)
    """
    max_retries        = max_retries        if max_retries        is not None else Config.MAX_RETRIES
    timeout            = timeout            if timeout            is not None else Config.REQUEST_TIMEOUT
    max_summary_length = max_summary_length if max_summary_length is not None else Config.MAX_SUMMARY_LENGTH

    image_url: Optional[str] = None
    body_text: str = ""

    # ── Step 1: Always fetch the actual webpage ───────────────────────────────
    logger.info("Fetching full article from: %s", url)
    html = _fetch_html(url, timeout=timeout, max_retries=max_retries)

    if html:
        # ── Step 2: Site-specific selectors (PRIMARY) ─────────────────────────
        body_text, image_url = _extract_via_selectors(html, url)

        # ── Step 3: readability-lxml ──────────────────────────────────────────
        if len(body_text.strip()) < 150:
            body_text, _ = _extract_via_readability(html, url)

        # ── Step 4: newspaper3k ───────────────────────────────────────────────
        if len(body_text.strip()) < 150:
            body_text, np_image = _extract_via_newspaper(url, html)
            if not image_url:
                image_url = np_image

        # ── Step 5: Generic body ──────────────────────────────────────────────
        if len(body_text.strip()) < 150:
            body_text, bs4_image = _extract_via_body(html, url)
            if not image_url:
                image_url = bs4_image

    # ── Step 6: RSS description — absolute last resort ────────────────────────
    if len(body_text.strip()) < 50:
        logger.warning(
            "All webpage strategies failed for %s — using RSS description fallback.", url
        )
        body_text = re.sub(r"<[^>]+>", "", fallback_description).strip()

    # ── Post-process ──────────────────────────────────────────────────────────
    body_text = _remove_duplicate_title(body_text, page_title)
    body_text = _clean_text(body_text)
    # Truncate to fit Telegram's message limit with a clean paragraph break
    result    = _truncate_for_telegram(body_text, max_summary_length)

    logger.info(
        "Final extraction: %d chars for %s | image=%s",
        len(result), url, "yes" if image_url else "no",
    )
    return result, image_url
