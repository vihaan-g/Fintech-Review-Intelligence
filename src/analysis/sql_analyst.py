"""Executes SQL analysis queries against the reviews database."""
import logging
import sqlite3
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class SQLAnalyst:
    """Runs SQL queries against reviews.db and returns structured results."""

    def __init__(self, db_path: str = "reviews.db") -> None:
        """Initialize with the path to the SQLite database.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path

    def run_query(self, sql: str) -> List[Dict[str, Any]]:
        """Execute a SQL query and return results as a list of row dicts."""
        raise NotImplementedError
