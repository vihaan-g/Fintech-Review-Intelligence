"""Runs all analytical SQL queries against the reviews database."""
import logging
import sqlite3
from typing import Any

from src.data_collection.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

DEFAULT_KEYWORDS: list[str] = [
    "kyc",
    "crash",
    "otp",
    "upi",
    "cashback",
    "freeze",
    "support",
    "slow",
    "failed",
    "blocked",
    "refund",
    "interest",
]


class SQLAnalyst:
    """Runs all analytical SQL queries against the reviews database.

    Each method corresponds to one analytical question.
    All queries are written to queries/analysis_queries.sql with comments.
    All queries use parameterized statements — no f-strings in SQL.
    """

    def __init__(self, db: DatabaseManager) -> None:
        """Initialise with a DatabaseManager already in context.

        Args:
            db: An open DatabaseManager context-managed instance.
        """
        self._db = db

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _execute(self, sql: str, params: tuple = ()) -> list[dict]:
        """Run a parameterized SELECT and return rows as list[dict].

        Args:
            sql:    SQL query string with ? placeholders.
            params: Tuple of values to bind.

        Returns:
            List of row dicts.

        Raises:
            sqlite3.Error: On any database error.
        """
        try:
            cursor = self._db._cursor()
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            logger.error("SQL execution failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Public analytical methods
    # ------------------------------------------------------------------

    def rating_distribution_over_time(self) -> list[dict]:
        """Monthly average rating per app for the last 12 months.

        Returns list of dicts: {app_name, month (YYYY-MM), avg_rating, review_count}
        Ordered by app_name, then month ascending.
        """
        logger.info("Running rating_distribution_over_time...")
        sql = """
        SELECT
            app_name,
            strftime('%Y-%m', date) AS month,
            ROUND(AVG(rating), 2)   AS avg_rating,
            COUNT(*)                AS review_count
        FROM reviews
        WHERE date >= date('now', '-12 months')
        GROUP BY app_name, month
        ORDER BY app_name ASC, month ASC
        """
        return self._execute(sql)

    def high_signal_low_rating_reviews(self, min_thumbs: int = 10) -> list[dict]:
        """Reviews with thumbs_up >= min_thumbs AND rating <= 2.

        These are validated pain points — other users agreed the complaint was valid.
        Returns list of dicts: {app_name, review_id, rating, thumbs_up, text, date}
        Ordered by thumbs_up descending.

        Args:
            min_thumbs: Minimum thumbs_up threshold (default 10).
        """
        logger.info("Running high_signal_low_rating_reviews (min_thumbs=%d)...", min_thumbs)
        sql = """
        SELECT
            app_name,
            review_id,
            rating,
            thumbs_up,
            text,
            date
        FROM reviews
        WHERE thumbs_up >= ?
          AND rating <= 2
        ORDER BY thumbs_up DESC
        """
        return self._execute(sql, (min_thumbs,))

    def developer_reply_impact(self) -> dict[str, dict]:
        """For each app: reply rate on 1-2 star reviews, and whether replies
        correlate with subsequent rating patterns.

        Returns dict keyed by app_name:
        {
            total_low_ratings: int,
            replied_count: int,
            reply_rate_pct: float,
            avg_rating_with_reply: float,
            avg_rating_without_reply: float
        }
        """
        logger.info("Running developer_reply_impact...")
        sql = """
        SELECT
            app_name,
            COUNT(*)                                        AS total_low_ratings,
            SUM(has_dev_reply)                              AS replied_count,
            ROUND(
                COALESCE(AVG(CASE WHEN has_dev_reply = 1 THEN CAST(rating AS FLOAT) END), 0.0), 2
            )                                               AS avg_rating_with_reply,
            ROUND(
                COALESCE(AVG(CASE WHEN has_dev_reply = 0 THEN CAST(rating AS FLOAT) END), 0.0), 2
            )                                               AS avg_rating_without_reply
        FROM reviews
        WHERE rating <= 2
        GROUP BY app_name
        """
        rows = self._execute(sql)
        result: dict[str, dict] = {}
        for row in rows:
            app = row["app_name"]
            total_low_ratings = int(row["total_low_ratings"])
            replied_count = int(row["replied_count"] or 0)
            reply_rate_pct = (
                (replied_count / total_low_ratings * 100) if total_low_ratings > 0 else 0.0
            )
            result[app] = {
                "total_low_ratings": total_low_ratings,
                "replied_count": replied_count,
                "reply_rate_pct": round(reply_rate_pct, 2),
                "avg_rating_with_reply": float(row["avg_rating_with_reply"]),
                "avg_rating_without_reply": float(row["avg_rating_without_reply"]),
            }
        return result

    def keyword_frequency(
        self, keywords: list[str] | None = None
    ) -> dict[str, dict[str, int]]:
        """Count keyword mentions per app using case-insensitive LIKE matching.

        Default keywords if none provided:
        ['kyc', 'crash', 'otp', 'upi', 'cashback', 'freeze', 'support',
         'slow', 'failed', 'blocked', 'refund', 'interest']

        Returns dict: {keyword: {app_name: count}}
        Only includes keywords with at least one match.

        Args:
            keywords: Optional list of keywords to search for.
        """
        logger.info("Running keyword_frequency...")
        kw_list = keywords if keywords is not None else DEFAULT_KEYWORDS
        result: dict[str, dict[str, int]] = {}

        for kw in kw_list:
            sql = """
            SELECT
                app_name,
                COUNT(*) AS mention_count
            FROM reviews
            WHERE LOWER(text) LIKE '%' || LOWER(?) || '%'
            GROUP BY app_name
            """
            rows = self._execute(sql, (kw,))
            if rows:
                result[kw] = {row["app_name"]: int(row["mention_count"]) for row in rows}

        return result

    def review_volume_by_week(self) -> list[dict]:
        """Weekly review volume and average rating per app.

        Returns list of dicts: {app_name, week (YYYY-WW), review_count, avg_rating}
        Spikes in volume often indicate update reactions or incidents.
        Ordered by app_name, then week ascending.
        """
        logger.info("Running review_volume_by_week...")
        sql = """
        SELECT
            app_name,
            strftime('%Y-%W', date) AS week,
            COUNT(*)                AS review_count,
            ROUND(AVG(rating), 2)   AS avg_rating
        FROM reviews
        GROUP BY app_name, week
        ORDER BY app_name ASC, week ASC
        """
        return self._execute(sql)

    def cross_app_summary(self) -> dict[str, dict]:
        """High-level summary stats per app.

        Returns dict keyed by app_name:
        {
            total_reviews: int,
            avg_rating: float,
            pct_one_star: float,
            pct_five_star: float,
            reply_rate_pct: float,
            most_common_rating: int
        }

        Note: SQLite has no MODE() aggregate function. most_common_rating is
        computed via a separate subquery using GROUP BY + ORDER BY COUNT(*) DESC
        LIMIT 1 per app.
        """
        logger.info("Running cross_app_summary...")
        sql = """
        SELECT
            app_name,
            COUNT(*)                                              AS total_reviews,
            ROUND(AVG(rating), 2)                                 AS avg_rating,
            ROUND(100.0 * SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                                  AS pct_one_star,
            ROUND(100.0 * SUM(CASE WHEN rating = 5 THEN 1 ELSE 0 END) / COUNT(*), 2)
                                                                  AS pct_five_star,
            ROUND(100.0 * SUM(has_dev_reply) / COUNT(*), 2)       AS reply_rate_pct
        FROM reviews
        GROUP BY app_name
        """
        rows = self._execute(sql)

        # SQLite has no MODE() function. most_common_rating is derived via a
        # per-app subquery: GROUP BY rating ORDER BY COUNT(*) DESC LIMIT 1.
        # The window function approach below achieves the same result set-wide.
        most_common_sql = """
        SELECT app_name, rating AS most_common_rating
        FROM (
            SELECT
                app_name,
                rating,
                COUNT(*) AS cnt,
                RANK() OVER (PARTITION BY app_name ORDER BY COUNT(*) DESC) AS rnk
            FROM reviews
            GROUP BY app_name, rating
        ) ranked
        WHERE rnk = 1
        """
        most_common_rows = self._execute(most_common_sql)
        most_common_map: dict[str, int] = {
            r["app_name"]: int(r["most_common_rating"]) for r in most_common_rows
        }

        result: dict[str, dict] = {}
        for row in rows:
            app = row["app_name"]
            result[app] = {
                "total_reviews": int(row["total_reviews"]),
                "avg_rating": float(row["avg_rating"] or 0.0),
                "pct_one_star": float(row["pct_one_star"] or 0.0),
                "pct_five_star": float(row["pct_five_star"] or 0.0),
                "reply_rate_pct": float(row["reply_rate_pct"] or 0.0),
                "most_common_rating": most_common_map.get(app, 0),
            }
        return result
