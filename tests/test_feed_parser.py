"""
Unit tests for the feed parser module.
"""

import pytest
from src.feed_parser import _extract_urls_from_text, _clean_url, _is_valid_url


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
