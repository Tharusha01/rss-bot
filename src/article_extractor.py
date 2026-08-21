"""
Article content extraction module.

Extraction priority (always fetches the real webpage first):
  1. Site-specific selectors  — div.news-content and other known Sri Lankan news layouts
                                Automatically follows iframes when content div is empty
  2. readability-lxml          — Mozilla algorithm; auto-removes ads/nav
  3. newspaper3k               — NLP-based fallback
  4. BeautifulSoup generic     — Generic body text extraction
  5. RSS description           — ABSOLUTE LAST RESORT only

Sinhala Unicode is fully preserved (UTF-8 enforced at every stage).
"""

import logging
import re
import time
from html import unescape as html_unescape
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from src.config import Config

logger = logging.getLogger(__name__)


def _supported_encodings() -> str:
    """
    Build the Accept-Encoding header.

    Brotli is only advertised when a decoder is actually installed — some
    sites (e.g. irinewslk.com) answer with Content-Encoding: br whenever the
    header allows it, and without the decoder requests hands back binary
    garbage instead of HTML, which silently breaks every extraction strategy.
    """
    try:
        import brotli  # noqa: F401
        return "gzip, deflate, br"
    except ImportError:
        pass
    try:
        import brotlicffi  # noqa: F401
        return "gzip, deflate, br"
    except ImportError:
        return "gzip, deflate"


# ── HTTP request headers ───────────────────────────────────────────────────────
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "si-LK,si;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": _supported_encodings(),
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

# ── Per-host selector overrides ───────────────────────────────────────────────
# Tried BEFORE the generic list above so a known site never falls through to a
# page-shell container ("main", "article") that drags in menus and widgets.
# Only the hosts listed here change behaviour; every other feed keeps using
# SITE_SPECIFIC_SELECTORS exactly as before.
# NOTE: an override list is authoritative — the generic selectors are NOT
# appended, because on these sites they match the wrong thing (irinewslk.com's
# div.post-content is a sidebar widget holding an unrelated older story).
SITE_SELECTOR_OVERRIDES: Dict[str, List[str]] = {
    # AdaDerana (Sinhala + English) — Next.js rebuild, Aug 2026.  div.news-content
    # no longer exists and the rebuilt page has no semantic containers left, so the
    # generic list fell through to "main" — which swallows the sidebar's
    # related-headline list and posted articles as a wall of other stories' titles
    # (131 chars of real body inside 3076 chars of links).  div.prose is Tailwind
    # Typography's content wrapper and holds the article body exactly.  The key
    # matches sinhala.adaderana.lk, adaderana.lk and www.adaderana.lk alike.
    "adaderana.lk": [
        "div.prose",
        "article div.prose",
    ],
    # Iri News — WordPress; the body is <p class="wp-block-paragraph"> inside
    # div.entry-content (the AddToAny share block in there is stripped as noise).
    "irinewslk.com": [
        "div.entry-content",
        "article div.entry-content",
        "div.post-body",
    ],
}

# Minimum body length for an extraction attempt to count as successful.
MIN_BODY_CHARS = 150
# Hosts with an explicit selector override are trusted at a lower bar — Iri News
# posts are often two short Sinhala paragraphs, and AdaDerana briefs run as short
# as ~130 chars; the generic 150-char floor would push both down to the truncated
# RSS excerpt (which, on AdaDerana's new feed, is just the headline again).
MIN_OVERRIDE_BODY_CHARS = 80


def _selectors_for(url: str) -> Tuple[List[str], int, bool]:
    """
    Return (selectors_to_try, min_body_chars, is_override) for *url*.

    Hosts listed in SITE_SELECTOR_OVERRIDES use only their own selectors;
    every other host keeps the generic list and the 150-char floor, unchanged.
    """
    host = urlparse(url).netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]

    for domain, selectors in SITE_SELECTOR_OVERRIDES.items():
        if host == domain or host.endswith("." + domain):
            return selectors, MIN_OVERRIDE_BODY_CHARS, True

    return SITE_SPECIFIC_SELECTORS, MIN_BODY_CHARS, False

# ── Noise patterns to remove before extraction ────────────────────────────────
_NOISE_TAG_NAMES = [
    "script", "style", "nav", "header", "footer",
    "aside", "form", "button", "iframe", "noscript", "ins",
]

# Embedded players — following these would pull the platform's own page
# boilerplate into the article body (Iri News embeds Facebook reels this way).
_EMBED_HOST_PATTERN = re.compile(
    r"(facebook\.com|fbcdn\.net|youtube\.com|youtu\.be|twitter\.com|x\.com"
    r"|instagram\.com|tiktok\.com|dailymotion\.com|vimeo\.com|soundcloud\.com"
    r"|doubleclick\.net|googlesyndication\.com)",
    re.IGNORECASE,
)

_NOISE_CLASS_PATTERN = re.compile(
    r"(advert|sponsor|related|social|share|comment|sidebar|widget"
    r"|breadcrumb|tag-cloud|newsletter|popup|banner|promo"
    r"|more-news|trending|latest-news-widget|read-also)",
    re.IGNORECASE,
)


# ── URL normalizer ────────────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    """
    Convert legacy PHP-style news URLs to their modern canonical form
    so that article content is served directly in the HTML (not JS-loaded).

    Rules applied:
      AdaDerana Sinhala:  .../news.php?nid=NNN  ->  .../news/NNN
      AdaDerana English:  .../news-1-NNN.html   ->  kept as-is (already works)

    Any unrecognised URL is returned unchanged.
    """
    parsed = urlparse(url)
    host   = parsed.netloc.lower()

    # ── AdaDerana Sinhala ─────────────────────────────────────────────────────
    # old: http://sinhala.adaderana.lk/news.php?nid=225638
    # new: https://sinhala.adaderana.lk/news/225638
    if "sinhala.adaderana.lk" in host and parsed.path.endswith("news.php"):
        qs = parse_qs(parsed.query)
        nid = qs.get("nid", [None])[0]
        if nid:
            normalized = f"https://sinhala.adaderana.lk/news/{nid}"
            logger.info("URL normalized: %s -> %s", url, normalized)
            return normalized

    # ── AdaDerana English (news.php?nid=NNN) ──────────────────────────────────
    # old: http://www.adaderana.lk/news.php?nid=99999
    # new: https://www.adaderana.lk/news/99999
    if "adaderana.lk" in host and "sinhala" not in host and parsed.path.endswith("news.php"):
        qs = parse_qs(parsed.query)
        nid = qs.get("nid", [None])[0]
        if nid:
            normalized = f"https://www.adaderana.lk/news/{nid}"
            logger.info("URL normalized: %s -> %s", url, normalized)
            return normalized

    return url


# ── Shared HTML downloader + iframe resolver ─────────────────────────────────

def _resolve_iframe_html(
    soup: BeautifulSoup,
    base_url: str,
    timeout: int,
    max_retries: int,
) -> Optional[str]:
    """
    If the page embeds content in an <iframe>, fetch the iframe's src URL
    and return its HTML.  This handles sites like AdaDerana Sinhala where
    div.news-content exists in the outer shell but is empty because the
    real article lives inside an iframe.

    Returns HTML string of the iframe page, or None if no iframe found.
    """
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "").strip()
        if not src:
            continue
        # Make relative srcs absolute
        if not src.startswith("http"):
            src = urljoin(base_url, src)
        # Skip video/social embeds — their page text is not the article
        if _EMBED_HOST_PATTERN.search(src):
            logger.debug("Skipping embed iframe: %s", src)
            continue
        # Skip ad/tracking iframes (small or clearly ad-related)
        width  = iframe.get("width",  "999")
        height = iframe.get("height", "999")
        try:
            if int(str(width).replace("%", "0"))  < 100: continue
            if int(str(height).replace("%", "0")) < 100: continue
        except ValueError:
            pass
        logger.info("Following iframe src: %s", src)
        iframe_html = _fetch_html(src, timeout=timeout, max_retries=max_retries)
        if iframe_html and len(iframe_html) > 500:
            return iframe_html
    return None


def _looks_like_html(text: Optional[str]) -> bool:
    """
    True if *text* is markup rather than an undecoded byte stream.

    A response whose Content-Encoding we cannot decode (e.g. brotli without the
    brotli package) still returns HTTP 200 and a non-empty body, so the only way
    to notice is to look at the payload.
    """
    if not text:
        return False
    head = text[:4000].lower()
    return "<html" in head or "<!doctype html" in head or "<body" in head


def _fetch_html(url: str, timeout: int, max_retries: int) -> Optional[str]:
    """
    Download raw HTML for *url* with retry + exponential back-off.
    Forces UTF-8 decoding so Sinhala characters are never mangled.

    If the body comes back as something other than markup (an encoding the
    installed libraries cannot decompress), the request is retried once with
    compression disabled before the attempt is considered failed.
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

            if _looks_like_html(resp.text):
                return resp.text

            logger.warning(
                "Response from %s is not readable HTML (Content-Encoding=%s) — "
                "retrying uncompressed.",
                url, resp.headers.get("Content-Encoding", "none"),
            )
            plain_headers = {**DEFAULT_HEADERS, "Accept-Encoding": "identity"}
            resp = requests.get(
                url,
                headers=plain_headers,
                timeout=timeout,
                allow_redirects=True,
            )
            resp.raise_for_status()
            resp.encoding = "utf-8"
            if _looks_like_html(resp.text):
                return resp.text

            raise ValueError("Response body is not decodable HTML")

        except (requests.RequestException, ValueError) as exc:
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
        if tag.decomposed:
            continue
        tag.decompose()

    # Remove by CSS class / id patterns.
    # decompose() destroys the tag AND its descendants, but those descendants are
    # still in the list we are iterating — touching one afterwards raises, so skip
    # anything already removed (e.g. the nested AddToAny share widget on Iri News).
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        classes = " ".join(tag.get("class", []) or [])
        tag_id  = tag.get("id", "") or ""
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
    timeout: int = 15,
    max_retries: int = 3,
    min_chars: int = MIN_BODY_CHARS,
) -> Tuple[str, Optional[str]]:
    """
    Try each selector returned by _selectors_for(url) in order.
    Returns the text of the first container that yields >= min_chars characters.

    If a selector matches but the element is empty (content loaded in an
    iframe), automatically fetches the iframe src and retries extraction
    on the iframe HTML.

    This is the PRIMARY strategy and covers all known Sri Lankan news sites.

    Returns:
        (body_text, image_url_or_None)
    """
    try:
        selectors, _, _is_override = _selectors_for(url)
        soup = BeautifulSoup(html, "html.parser")  # pure-Python, handles malformed HTML
        image_url = _og_image(soup)

        # ── Check for iframe-embedded content ─────────────────────────────────
        # If any known selector exists but is empty, the content is likely
        # inside an iframe (e.g. AdaDerana Sinhala).
        for selector in selectors:
            el = soup.select_one(selector)
            if el is not None and len(el.get_text(strip=True)) < 50:
                # Element found but empty — check for iframes
                iframe_html = _resolve_iframe_html(
                    soup, base_url=url, timeout=timeout, max_retries=max_retries
                )
                if iframe_html:
                    logger.info(
                        "Selector '%s' was empty; retrying on iframe HTML", selector
                    )
                    # Recurse into the iframe HTML (no further iframe following)
                    iframe_soup = BeautifulSoup(iframe_html, "html.parser")
                    if not image_url:
                        image_url = _og_image(iframe_soup)
                    # Replace soup so the selector loop below works on iframe content
                    soup = iframe_soup
                break  # only check once

        for selector in selectors:
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

            if len(body.strip()) >= min_chars:
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

    # ── Step 1: Normalize URL, then always fetch the actual webpage ───────────
    url = _normalize_url(url)
    _, min_chars, is_override = _selectors_for(url)
    logger.info("Fetching full article from: %s", url)
    html = _fetch_html(url, timeout=timeout, max_retries=max_retries)

    if html:
        # ── Step 2: Site-specific selectors (PRIMARY) ─────────────────────────
        body_text, image_url = _extract_via_selectors(
            html, url, timeout=timeout, max_retries=max_retries, min_chars=min_chars
        )

        # ── Step 3: readability-lxml ──────────────────────────────────────────
        if len(body_text.strip()) < min_chars:
            body_text, _ = _extract_via_readability(html, url)

        # ── Step 4: newspaper3k ───────────────────────────────────────────────
        if len(body_text.strip()) < min_chars:
            body_text, np_image = _extract_via_newspaper(url, html)
            if not image_url:
                image_url = np_image

        # ── Step 5: Generic body ──────────────────────────────────────────────
        # Skipped for override hosts: we know exactly where their body lives, so
        # an empty container means the post has no text (e.g. an Iri News video
        # post) and dumping the whole page would post navigation as the article.
        if len(body_text.strip()) < min_chars and not is_override:
            body_text, bs4_image = _extract_via_body(html, url)
            if not image_url:
                image_url = bs4_image

    # ── Step 6: RSS description — absolute last resort ────────────────────────
    if len(body_text.strip()) < 50:
        logger.warning(
            "All webpage strategies failed for %s — using RSS description fallback.", url
        )
        # unescape() so a double-encoded feed entity (&#8230;) never reaches Telegram
        body_text = html_unescape(re.sub(r"<[^>]+>", "", fallback_description)).strip()

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
