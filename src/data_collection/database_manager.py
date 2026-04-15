"""Manages SQLite database connections and schema for the review pipeline."""
import logging
import sqlite3
from typing import List

logger = logging.getLogger(__name__)


class DatabaseManager:
    """Manages the SQLite database for storing and querying reviews."""

    def __init__(self, db_path: str = "reviews.db") -> None:
        """Initialize the database manager.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        """Open a WAL-mode SQLite connection."""
        raise NotImplementedError

    def insert_reviews(self, reviews: List[dict]) -> int:
        """Insert a batch of reviews and return the count inserted."""
        raise NotImplementedError

    def close(self) -> None:
        """Close the database connection."""
        raise NotImplementedError
