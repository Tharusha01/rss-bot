"""
Quick debug script - paste any article URL to see what each extraction
strategy finds, including iframe detection.

Usage:
  .venv\\Scripts\\python debug_extract.py <article_url>

Example:
  .venv\\Scripts\\python debug_extract.py https://sinhala.adaderana.lk/news/225638
"""

import sys
# Force UTF-8 output so Sinhala characters print correctly in Windows terminal
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "si-LK,si;q=0.9,en-US;q=0.8",
}

SELECTORS_TO_TEST = [
    "div.news-content",
    "div.article-body",
    "div.story-body",
    "div.entry-content",
    "div.post-content",
    "div.single-blog-post",
    "div.field-item",
    "div.field-items",
    "div.td-post-content",
    "article",
    "main",
]


def _normalize_url(url: str) -> str:
    """Mirror of src/article_extractor._normalize_url for debug use."""
    from urllib.parse import urlparse, parse_qs
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if "sinhala.adaderana.lk" in host and parsed.path.endswith("news.php"):
        nid = parse_qs(parsed.query).get("nid", [None])[0]
        if nid:
            return f"https://sinhala.adaderana.lk/news/{nid}"
    if "adaderana.lk" in host and "sinhala" not in host and parsed.path.endswith("news.php"):
        nid = parse_qs(parsed.query).get("nid", [None])[0]
        if nid:
            return f"https://www.adaderana.lk/news/{nid}"
    return url


def fetch(url: str):
    resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    resp.raise_for_status()
    if "charset=utf-8" in resp.headers.get("Content-Type", "").lower():
        resp.encoding = "utf-8"
    else:
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text, resp.status_code, resp.encoding


def find_iframes(soup: BeautifulSoup, base_url: str):
    """Return list of (src, width, height) for all iframes."""
    results = []
    for iframe in soup.find_all("iframe"):
        src = iframe.get("src", "")
        if src and not src.startswith("http"):
            from urllib.parse import urljoin
            src = urljoin(base_url, src)
        results.append({
            "src": src,
            "width": iframe.get("width", "?"),
            "height": iframe.get("height", "?"),
        })
    return results


def test_selectors(soup: BeautifulSoup, label=""):
    print(f"\n--- Selector results {label} ---")
    found = None
    for sel in SELECTORS_TO_TEST:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(separator="\n", strip=True)
            print(f"  {'OK' if text else 'EMPTY'} {sel:<35} -> {len(text):>5} chars")
            if text and not found:
                found = (sel, el)
        else:
            print(f"  -- {sel:<35} -> NOT FOUND")
    return found


def show_content(selector, el):
    for tag in el.find_all(["script", "style", "ins"]):
        tag.decompose()
    paragraphs = [p.get_text(strip=True) for p in el.find_all("p") if p.get_text(strip=True)]
    if paragraphs:
        full = "\n\n".join(paragraphs)
    else:
        full = el.get_text(separator="\n", strip=True)
    print(f"\n=== CONTENT via [{selector}] === {len(full)} chars, {len(paragraphs)} paragraphs ===")
    print(full[:8000])
    if len(full) > 8000:
        print(f"\n... [{len(full)-8000} more chars] ...")


def debug(url: str):
    # Normalize the URL first (e.g. news.php?nid=NNN -> news/NNN)
    normalized = _normalize_url(url)
    if normalized != url:
        print(f"\n  Original URL : {url}")
        print(f"  Normalized   : {normalized}")
        url = normalized

    print(f"\n{'='*70}")
    print(f"  URL: {url}")
    print(f"{'='*70}\n")

    try:
        html, status, enc = fetch(url)
        print(f"HTTP {status} | encoding={enc} | {len(html)} chars\n")
    except Exception as e:
        print(f"FAILED to fetch: {e}")
        return

    soup = BeautifulSoup(html, "html.parser")

    # Show iframes first
    iframes = find_iframes(soup, url)
    if iframes:
        print(f"IFRAMES FOUND ({len(iframes)}):")
        for i, f in enumerate(iframes, 1):
            print(f"  [{i}] src={f['src']}  size={f['width']}x{f['height']}")
    else:
        print("No iframes found.")

    # Test selectors on main page
    match = test_selectors(soup, label="(main page)")

    if match:
        show_content(*match)
    elif iframes:
        # Try fetching each iframe's src
        for i, f in enumerate(iframes, 1):
            if not f["src"]:
                continue
            print(f"\nFetching iframe [{i}]: {f['src']}")
            try:
                iframe_html, _, iframe_enc = fetch(f["src"])
                print(f"  HTTP 200 | {len(iframe_html)} chars | enc={iframe_enc}")
                iframe_soup = BeautifulSoup(iframe_html, "html.parser")
                # Show div classes in iframe
                classes = sorted({c for d in iframe_soup.find_all("div", class_=True) for c in d.get("class", [])})
                print(f"  Div classes in iframe: {', '.join(classes[:30])}")
                iframe_match = test_selectors(iframe_soup, label=f"(iframe {i})")
                if iframe_match:
                    show_content(*iframe_match)
                    break
            except Exception as e:
                print(f"  Failed to fetch iframe: {e}")
    else:
        print("\nNo content found with any selector.")
        print("Raw <body> (first 1500 chars):")
        body = soup.find("body")
        if body:
            print(body.get_text(separator="\n", strip=True)[:1500])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: .venv\\Scripts\\python debug_extract.py <article_url>")
        sys.exit(1)
    debug(sys.argv[1])
