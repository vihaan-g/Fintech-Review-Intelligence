"""Manages SQLite database connections, schema, and all read/write operations."""
import json
import logging
import os
import sqlite3
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

_CREATE_REVIEWS = """
CREATE TABLE IF NOT EXISTS reviews (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    app_name         TEXT NOT NULL,
    review_id        TEXT UNIQUE NOT NULL,
    rating           INTEGER NOT NULL,
    text             TEXT NOT NULL,
    date             TEXT NOT NULL,
    thumbs_up        INTEGER DEFAULT 0,
    has_dev_reply    INTEGER DEFAULT 0,
    dev_reply_text   TEXT,
    scraped_at       TEXT NOT NULL,
    classification   TEXT
)
"""

_CREATE_PIPELINE_STATE = """
CREATE TABLE IF NOT EXISTS pipeline_state (
    phase       TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    metadata    TEXT
)
"""

# Optional review fields that default to None when absent from the input dict.
_OPTIONAL_REVIEW_FIELDS: dict[str, Any] = {
    "classification": None,
    "dev_reply_text": None,
    "thumbs_up": 0,
    "has_dev_reply": 0,
}


class DatabaseManager:
    """Manages the SQLite database connection, schema, and all read/write operations.

    Uses WAL mode for concurrent reads. Acts as context manager.
    """

    def __init__(self, db_path: str = "outputs/reviews.db") -> None:
        """Initialise the manager with a path to the SQLite file.

        Args:
            db_path: Filesystem path to the SQLite database. Use ':memory:' for tests.
        """
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "DatabaseManager":
        """Open connection, enable WAL mode, return self."""
        try:
            db_dir = os.path.dirname(self.db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()
            logger.debug("Opened SQLite connection: %s", self.db_path)
        except sqlite3.Error as exc:
            logger.error("Failed to open database at %s: %s", self.db_path, exc)
            raise
        return self

    def __exit__(
        self,
        exc_type: type | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Commit on clean exit, rollback on exception, always close connection."""
        if self._conn is None:
            return
        try:
            if exc_type is None:
                self._conn.commit()
                logger.debug("Committed transaction on clean exit.")
            else:
                self._conn.rollback()
                logger.warning("Rolled back transaction due to exception: %s", exc_val)
        except sqlite3.Error as exc:
            logger.error("Error during connection cleanup: %s", exc)
        finally:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        """Expose the underlying connection for read-only inspection (e.g. tests).

        Raises:
            RuntimeError: If called outside a context manager block.
        """
        if self._conn is None:
            raise RuntimeError(
                "DatabaseManager must be used as a context manager (with statement)."
            )
        return self._conn

    def _cursor(self) -> sqlite3.Cursor:
        """Return a cursor, raising RuntimeError if not inside a context block."""
        if self._conn is None:
            raise RuntimeError(
                "DatabaseManager must be used as a context manager (with statement)."
            )
        return self._conn.cursor()

    def create_schema(self) -> None:
        """Create all tables if they do not exist.

        Tables created:
          - reviews: stores raw Play Store review data.
          - pipeline_state: tracks completion status of each pipeline phase.
        """
        try:
            conn = self._conn  # unwrap Optional once for the whole block
            assert conn is not None
            cursor = conn.cursor()
            cursor.execute(_CREATE_REVIEWS)
            cursor.execute(_CREATE_PIPELINE_STATE)
            # Performance indexes for analytical queries
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reviews_app_name "
                "ON reviews(app_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reviews_date "
                "ON reviews(date)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reviews_rating_thumbs "
                "ON reviews(rating, thumbs_up)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_reviews_classification "
                "ON reviews(classification)"
            )
            conn.commit()
            logger.info("Schema verified / created.")
        except sqlite3.Error as exc:
            logger.error("Failed to create schema: %s", exc)
            raise

    def insert_reviews(self, reviews: list[dict]) -> int:
        """Insert a list of review dicts. Skip duplicates via INSERT OR IGNORE.

        Optional fields (classification, dev_reply_text, thumbs_up,
        has_dev_reply) are defaulted to None/0 when absent from the input dict,
        so callers do not need to supply every column.

        Returns the number of rows actually inserted.

        Args:
            reviews: List of review dicts matching the reviews table schema.

        Returns:
            int: Count of new rows inserted (duplicates are silently skipped).
        """
        if not reviews:
            return 0

        count_before = self._count_all_reviews()

        sql = """
        INSERT OR IGNORE INTO reviews
            (app_name, review_id, rating, text, date, thumbs_up,
             has_dev_reply, dev_reply_text, scraped_at, classification)
        VALUES
            (:app_name, :review_id, :rating, :text, :date, :thumbs_up,
             :has_dev_reply, :dev_reply_text, :scraped_at, :classification)
        """
        # Normalize each row: fill in optional fields without mutating the
        # original dicts passed by the caller.
        normalized = [
            {**_OPTIONAL_REVIEW_FIELDS, **review}
            for review in reviews
        ]
        try:
            cursor = self._cursor()
            cursor.executemany(sql, normalized)
            self._conn.commit()  # type: ignore[union-attr]
        except sqlite3.Error as exc:
            logger.error("Failed to insert reviews: %s", exc)
            raise

        count_after = self._count_all_reviews()
        inserted = count_after - count_before
        logger.debug("Inserted %d new reviews (%d skipped).", inserted, len(reviews) - inserted)
        return inserted

    def _count_all_reviews(self) -> int:
        """Return total row count in the reviews table (internal helper)."""
        try:
            cursor = self._cursor()
            cursor.execute("SELECT COUNT(*) FROM reviews")
            row = cursor.fetchone()
            return row[0] if row else 0
        except sqlite3.Error as exc:
            logger.error("Failed to count reviews: %s", exc)
            raise

    def get_review_count(self, app_name: str | None = None) -> int:
        """Return total review count. If app_name provided, count for that app only.

        Args:
            app_name: Optional app name filter.

        Returns:
            int: Number of matching reviews.
        """
        try:
            cursor = self._cursor()
            if app_name is not None:
                cursor.execute(
                    "SELECT COUNT(*) FROM reviews WHERE app_name = ?", (app_name,)
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM reviews")
            row = cursor.fetchone()
            return row[0] if row else 0
        except sqlite3.Error as exc:
            logger.error("Failed to get review count: %s", exc)
            raise

    def save_phase_state(
        self, phase: str, status: str, metadata: dict | None = None
    ) -> None:
        """Upsert phase status into pipeline_state table.

        Args:
            phase: Pipeline phase identifier (e.g. 'collection').
            status: One of 'pending', 'in_progress', 'complete', 'failed'.
            metadata: Optional dict of phase-specific data, stored as JSON.
        """
        updated_at = datetime.now().isoformat()
        metadata_json = json.dumps(metadata) if metadata is not None else None

        sql = """
        INSERT OR REPLACE INTO pipeline_state (phase, status, updated_at, metadata)
        VALUES (?, ?, ?, ?)
        """
        try:
            cursor = self._cursor()
            cursor.execute(sql, (phase, status, updated_at, metadata_json))
            self._conn.commit()  # type: ignore[union-attr]
            logger.debug("Saved phase state: phase=%s status=%s", phase, status)
        except sqlite3.Error as exc:
            logger.error("Failed to save phase state for '%s': %s", phase, exc)
            raise

    def get_phase_state(self, phase: str) -> dict | None:
        """Return pipeline_state row as dict, or None if phase not found.

        Args:
            phase: Pipeline phase identifier.

        Returns:
            dict with keys (phase, status, updated_at, metadata) or None.
        """
        try:
            cursor = self._cursor()
            cursor.execute(
                "SELECT phase, status, updated_at, metadata FROM pipeline_state WHERE phase = ?",
                (phase,),
            )
            row = cursor.fetchone()
        except sqlite3.Error as exc:
            logger.error("Failed to get phase state for '%s': %s", phase, exc)
            raise

        if row is None:
            return None

        result: dict = dict(row)
        if result.get("metadata") is not None:
            try:
                result["metadata"] = json.loads(result["metadata"])
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("Could not parse metadata JSON for phase '%s': %s", phase, exc)
        return result

    def get_unclassified_reviews(self, limit: int | None = None) -> list[dict]:
        """Return reviews where classification IS NULL, up to limit rows.

        Args:
            limit: Maximum number of rows to return. None means no limit.

        Returns:
            List of review dicts.
        """
        sql = "SELECT * FROM reviews WHERE classification IS NULL"
        params: tuple = ()

        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)

        try:
            cursor = self._cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            logger.error("Failed to fetch unclassified reviews: %s", exc)
            raise

    def update_classification(self, review_id: str, classification: str) -> None:
        """Save JSON classification string for a single review.

        Args:
            review_id: The unique review identifier (review_id column).
            classification: JSON string from the classifier.
        """
        sql = "UPDATE reviews SET classification = ? WHERE review_id = ?"
        try:
            cursor = self._cursor()
            cursor.execute(sql, (classification, review_id))
            self._conn.commit()  # type: ignore[union-attr]
            logger.debug("Updated classification for review_id=%s", review_id)
        except sqlite3.Error as exc:
            logger.error(
                "Failed to update classification for review_id '%s': %s", review_id, exc
            )
            raise
