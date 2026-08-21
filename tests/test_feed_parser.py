"""
Unit tests for the feed parser module.
"""

import pytest
from src.feed_parser import (
    _extract_urls_from_text,
    _clean_url,
    _is_valid_url,
    _rewrite_host,
)


def test_extract_urls_from_plain_text():
    text = "Check out https://www.adaderana.lk/news.php?nid=12345 for details."
    urls = _extract_urls_from_text(text)
    assert "https://www.adaderana.lk/news.php?nid=12345" in urls


def test_extract_multiple_urls():
    text = "Visit https://site1.com and http://site2.lk/article for news."
    urls = _extract_urls_from_text(text)
    assert len(urls) == 2


def test_extract_urls_sinhala_mixed():
    """URLs should be extracted even when surrounded by Sinhala Unicode text."""
    text = "මෙය ලිපිය https://sinhala-news.lk/article/123 කියවන්න"
    urls = _extract_urls_from_text(text)
    assert "https://sinhala-news.lk/article/123" in urls


def test_clean_url_strips_trailing_punctuation():
    assert _clean_url("https://example.com/article.") == "https://example.com/article"
    assert _clean_url("https://example.com/article,") == "https://example.com/article"
    assert _clean_url("https://example.com/article)") == "https://example.com/article"


def test_is_valid_url_http():
    assert _is_valid_url("http://example.com") is True
    assert _is_valid_url("https://example.com/path?q=1") is True


def test_is_valid_url_rejects_bad():
    assert _is_valid_url("not-a-url") is False
    assert _is_valid_url("ftp://example.com") is False
    assert _is_valid_url("") is False


def test_rewrite_host_adaderana_sinhala():
    """
    The rss.xml feed links to adaderanasinhala.lk, which 301s every path to the
    site root — so the path must be carried over to the canonical host instead.
    """
    assert (
        _rewrite_host("https://adaderanasinhala.lk/news/250130")
        == "https://sinhala.adaderana.lk/news/250130"
    )
    assert (
        _rewrite_host("https://www.adaderanasinhala.lk/news/250130")
        == "https://sinhala.adaderana.lk/news/250130"
    )


def test_rewrite_host_preserves_query_and_upgrades_scheme():
    assert (
        _rewrite_host("http://adaderanasinhala.lk/news/1?x=2")
        == "https://sinhala.adaderana.lk/news/1?x=2"
    )


def test_rewrite_host_leaves_other_hosts_untouched():
    for url in (
        "https://sinhala.adaderana.lk/news/250130",
        "https://irinewslk.com/feed/",
        "https://adaderana.lk/news/cmt2ezelm0004356pm9bgfgai",
        "",
    ):
        assert _rewrite_host(url) == url
