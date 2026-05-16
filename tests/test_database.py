"""
Unit tests for the database layer.
Uses an in-memory SQLite database for isolation.
"""

import pytest
from src.database import Database


@pytest.fixture
def db(tmp_path):
    """Provide a fresh Database instance backed by a temp file."""
    return Database(str(tmp_path / "test.db"))


# ── Feed management ────────────────────────────────────────────────────────────

def test_add_feed_success(db):
    result = db.add_feed("https://example.com/rss", added_by=1, channel_id="@test")
    assert result is True
    feeds = db.get_active_feeds()
    assert len(feeds) == 1
    assert feeds[0]["url"] == "https://example.com/rss"


def test_add_feed_duplicate_returns_false(db):
    db.add_feed("https://example.com/rss", added_by=1, channel_id="@test")
    result = db.add_feed("https://example.com/rss", added_by=1, channel_id="@test")
    assert result is False


def test_remove_feed(db):
    db.add_feed("https://example.com/rss", added_by=1, channel_id="@test")
    result = db.remove_feed("https://example.com/rss")
    assert result is True
    assert db.get_active_feeds() == []


def test_remove_nonexistent_feed(db):
    result = db.remove_feed("https://nope.com/rss")
    assert result is False


def test_readd_removed_feed(db):
    """Re-adding a soft-deleted feed should succeed and reactivate it."""
    db.add_feed("https://example.com/rss", added_by=1, channel_id="@test")
    db.remove_feed("https://example.com/rss")
    result = db.add_feed("https://example.com/rss", added_by=1, channel_id="@test")
    assert result is True
    assert len(db.get_active_feeds()) == 1


# ── Deduplication ──────────────────────────────────────────────────────────────

def test_article_not_sent_initially(db):
    db.add_feed("https://example.com/rss", added_by=1, channel_id="@test")
    feed_id = db.get_active_feeds()[0]["id"]
    assert db.is_article_sent(feed_id, "https://article.com/1") is False


def test_mark_and_check_article_sent(db):
    db.add_feed("https://example.com/rss", added_by=1, channel_id="@test")
    feed_id = db.get_active_feeds()[0]["id"]
    db.mark_article_sent(feed_id, "https://article.com/1")
    assert db.is_article_sent(feed_id, "https://article.com/1") is True


def test_mark_article_sent_idempotent(db):
    """Marking the same article twice must not raise an error."""
    db.add_feed("https://example.com/rss", added_by=1, channel_id="@test")
    feed_id = db.get_active_feeds()[0]["id"]
    db.mark_article_sent(feed_id, "https://article.com/1")
    db.mark_article_sent(feed_id, "https://article.com/1")  # should not raise
    assert db.is_article_sent(feed_id, "https://article.com/1") is True


# ── Stats ──────────────────────────────────────────────────────────────────────

def test_get_stats(db):
    db.add_feed("https://example.com/rss", added_by=1, channel_id="@test")
    feed_id = db.get_active_feeds()[0]["id"]
    db.mark_article_sent(feed_id, "https://article.com/1")
    stats = db.get_stats()
    assert stats["active_feeds"] == 1
    assert stats["sent_articles"] == 1
