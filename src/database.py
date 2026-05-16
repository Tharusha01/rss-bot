"""
Database layer for the RSS News Bot.
Manages feed storage, sent-article deduplication, and periodic cleanup.
All operations use a connection-per-call pattern for thread safety.
"""

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Generator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Schema ─────────────────────────────────────────────────────────────────────
SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS feeds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT    NOT NULL UNIQUE,
    added_by    INTEGER NOT NULL,          -- Telegram user_id of the admin who added it
    channel_id  TEXT    NOT NULL,          -- Target Telegram channel/group
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS sent_articles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id     INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
    article_url TEXT    NOT NULL,
    sent_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE (feed_id, article_url)
);

CREATE INDEX IF NOT EXISTS idx_sent_articles_feed_id  ON sent_articles(feed_id);
CREATE INDEX IF NOT EXISTS idx_sent_articles_sent_at  ON sent_articles(sent_at);
CREATE INDEX IF NOT EXISTS idx_feeds_channel          ON feeds(channel_id);
"""


class Database:
    """Thin wrapper around SQLite providing feed and dedup management."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._ensure_dir()
        self._init_schema()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _ensure_dir(self) -> None:
        """Create parent directories for the database file if needed."""
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    @contextmanager
    def _get_conn(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a thread-local connection with row_factory set."""
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        """Apply the schema (idempotent — safe to run on every startup)."""
        with self._get_conn() as conn:
            conn.executescript(SCHEMA_SQL)
        logger.info("Database schema initialised at %s", self.db_path)

    # ── Feed management ────────────────────────────────────────────────────────

    def add_feed(self, url: str, added_by: int, channel_id: str) -> bool:
        """
        Insert a new RSS feed.

        Returns:
            True  if the feed was inserted.
            False if the URL already exists (including inactive ones).
        """
        with self._get_conn() as conn:
            # Re-activate if it was previously removed
            existing = conn.execute(
                "SELECT id, is_active FROM feeds WHERE url = ?", (url,)
            ).fetchone()
            if existing:
                if existing["is_active"]:
                    return False  # already active
                conn.execute(
                    "UPDATE feeds SET is_active = 1, channel_id = ?, added_by = ? WHERE url = ?",
                    (channel_id, added_by, url),
                )
                logger.info("Re-activated feed: %s", url)
                return True
            conn.execute(
                "INSERT INTO feeds (url, added_by, channel_id) VALUES (?, ?, ?)",
                (url, added_by, channel_id),
            )
            logger.info("Added new feed: %s (channel=%s)", url, channel_id)
            return True

    def remove_feed(self, url: str) -> bool:
        """
        Soft-delete a feed by marking it inactive.

        Returns:
            True  if a feed was deactivated.
            False if no matching active feed was found.
        """
        with self._get_conn() as conn:
            cursor = conn.execute(
                "UPDATE feeds SET is_active = 0 WHERE url = ? AND is_active = 1", (url,)
            )
            removed = cursor.rowcount > 0
            if removed:
                logger.info("Removed feed: %s", url)
            return removed

    def get_active_feeds(self) -> List[sqlite3.Row]:
        """Return all active feeds as a list of Row objects."""
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM feeds WHERE is_active = 1 ORDER BY created_at"
            ).fetchall()

    def get_feed_by_url(self, url: str) -> Optional[sqlite3.Row]:
        """Look up an active feed by its URL."""
        with self._get_conn() as conn:
            return conn.execute(
                "SELECT * FROM feeds WHERE url = ? AND is_active = 1", (url,)
            ).fetchone()

    # ── Deduplication ──────────────────────────────────────────────────────────

    def is_article_sent(self, feed_id: int, article_url: str) -> bool:
        """Return True if the article has already been posted."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM sent_articles WHERE feed_id = ? AND article_url = ?",
                (feed_id, article_url),
            ).fetchone()
            return row is not None

    def mark_article_sent(self, feed_id: int, article_url: str) -> None:
        """Record that an article has been sent (ignore if duplicate)."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sent_articles (feed_id, article_url) VALUES (?, ?)",
                (feed_id, article_url),
            )

    # ── Maintenance ────────────────────────────────────────────────────────────

    def cleanup_old_records(self, days: int = 30) -> int:
        """
        Delete sent_article records older than *days*.

        Returns:
            Number of rows deleted.
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self._get_conn() as conn:
            cursor = conn.execute(
                "DELETE FROM sent_articles WHERE sent_at < ?", (cutoff,)
            )
            deleted = cursor.rowcount
            if deleted:
                logger.info("Cleaned up %d old sent_articles records (older than %d days)", deleted, days)
            return deleted

    def get_stats(self) -> dict:
        """Return a simple stats dict for admin use."""
        with self._get_conn() as conn:
            feeds_total    = conn.execute("SELECT COUNT(*) FROM feeds WHERE is_active=1").fetchone()[0]
            articles_total = conn.execute("SELECT COUNT(*) FROM sent_articles").fetchone()[0]
            return {"active_feeds": feeds_total, "sent_articles": articles_total}
