"""Scrapes Play Store reviews for Indian fintech apps and stores them in SQLite."""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from google_play_scraper import Sort, reviews

from src.config import Config
from src.data_collection.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

_SLEEP_BETWEEN_PAGES: float = 0.5
_SLEEP_BETWEEN_APPS: float = 2.0
_EMPTY_PAGE_MAX_RETRIES: int = 3
_EMPTY_PAGE_BACKOFF_BASE: float = 5.0


@dataclass
class CollectionResult:
    """Result summary from a full collection run."""

    total_collected: int
    per_app: dict[str, int]
    skipped_apps: list[str]
    duration_seconds: float


class ReviewCollector:
    """Scrapes Play Store reviews for Indian fintech apps and stores them via DatabaseManager.

    Checks pipeline_state before each app — skips apps already marked complete.
    Checkpoints after each app completes, not at the end of the full run.
    """

    APP_TARGETS: dict[str, str] = {
        "Fi Money": "com.epifi.fi",
        "Jupiter": "org.jupiter.app",
        "CRED": "com.dreamplug.androidapp",
        "PhonePe": "com.phonepe.app",
    }

    def __init__(self, db: DatabaseManager, config: Config) -> None:
        """Initialise the collector with a DatabaseManager and Config.

        Args:
            db: Open DatabaseManager instance (must be used as context manager by caller).
            config: Validated Config instance (not used directly by scraping, kept for
                    consistency with pipeline conventions and future auth needs).
        """
        self._db = db
        self._config = config

    def collect_all(self, target_per_app: int = 2500) -> CollectionResult:
        """Run collection for all apps. Returns CollectionResult.

        For each app:
          1. Check pipeline_state — if status is 'complete', skip entirely.
          2. Mark status 'in_progress'.
          3. Call collect_app().
          4. Insert results into DB.
          5. Mark status 'complete' with metadata = {count: n, timestamp: ...}.

        Args:
            target_per_app: How many reviews to target per app.

        Returns:
            CollectionResult summarising what was collected.
        """
        start = datetime.now(timezone.utc)
        per_app: dict[str, int] = {}
        skipped_apps: list[str] = []
        apps_to_collect = list(self.APP_TARGETS.items())

        for idx, (app_name, app_id) in enumerate(apps_to_collect):
            phase_key = f"collection_{app_name.lower().replace(' ', '_')}"
            state = self._db.get_phase_state(phase_key)

            if state is not None and state.get("status") == "complete":
                logger.info("Skipping %s — already complete", app_name)
                skipped_apps.append(app_name)
                continue

            # Small cooldown between apps to avoid tripping Play Store rate
            # limits. Skip on the first iteration (nothing to cool from).
            if idx > 0:
                time.sleep(_SLEEP_BETWEEN_APPS)

            self._db.save_phase_state(phase_key, "in_progress")

            try:
                collected = self.collect_app(app_id, app_name, target_per_app)
                inserted = self._db.insert_reviews(collected)
            except Exception as exc:
                logger.error("Failed to collect reviews for %s: %s", app_name, exc)
                self._db.save_phase_state(phase_key, "failed", {"error": str(exc)})
                per_app[app_name] = 0
                continue

            per_app[app_name] = inserted
            self._db.save_phase_state(
                phase_key,
                "complete",
                {"count": inserted, "timestamp": datetime.now(timezone.utc).isoformat()},
            )
            logger.info("Completed collection for %s: %d reviews inserted.", app_name, inserted)

        duration = (datetime.now(timezone.utc) - start).total_seconds()
        total = sum(per_app.values())
        return CollectionResult(
            total_collected=total,
            per_app=per_app,
            skipped_apps=skipped_apps,
            duration_seconds=duration,
        )

    def collect_app(self, app_id: str, app_name: str, count: int) -> list[dict]:
        """Scrape reviews for one app using google-play-scraper.

        Uses continuation tokens to paginate past the 200-review default limit.
        Adds 0.5s sleep between pagination calls (rate limit protection).

        Args:
            app_id: Play Store package identifier (e.g. 'com.epifi.fi').
            app_name: Human-readable app name added to every review dict.
            count: Target number of reviews to collect.

        Returns:
            List of normalised review dicts matching the reviews table schema.
        """
        collected: list[dict] = []
        continuation_token = None

        while len(collected) < count:
            batch_size = min(200, count - len(collected))
            try:
                result, continuation_token = reviews(
                    app_id,
                    lang="en",
                    country="in",
                    sort=Sort.NEWEST,
                    count=batch_size,
                    continuation_token=continuation_token,
                )
            except Exception as exc:
                logger.error(
                    "Error fetching reviews for %s (page token=%s): %s",
                    app_name,
                    continuation_token,
                    exc,
                )
                break

            if not result:
                # B5: empty page may be a Play Store rate-limit, not true end-of-stream.
                # Retry with backoff on the first page; treat empty as end-of-stream
                # only when a continuation token was previously returned.
                had_token_before = continuation_token is not None
                if not had_token_before and len(collected) == 0:
                    # First page with no prior token — could be throttle, retry.
                    empty_page_retry = 0
                    while empty_page_retry < _EMPTY_PAGE_MAX_RETRIES:
                        delay = _EMPTY_PAGE_BACKOFF_BASE * (2 ** empty_page_retry)
                        logger.warning(
                            "Empty first page for %s — possible rate-limit, "
                            "sleeping %.1fs before retry %d/%d",
                            app_name, delay, empty_page_retry + 1, _EMPTY_PAGE_MAX_RETRIES,
                        )
                        time.sleep(delay)
                        try:
                            result, continuation_token = reviews(
                                app_id,
                                lang="en",
                                country="in",
                                sort=Sort.NEWEST,
                                count=batch_size,
                                continuation_token=None,
                            )
                        except Exception as retry_exc:
                            logger.error(
                                "Retry fetch failed for %s: %s", app_name, retry_exc
                            )
                            result = []
                        if result:
                            break
                        empty_page_retry += 1
                    if not result:
                        logger.info(
                            "No reviews returned for %s after %d retries — skipping.",
                            app_name, _EMPTY_PAGE_MAX_RETRIES,
                        )
                        break
                else:
                    logger.info(
                        "No more reviews returned for %s after %d collected.",
                        app_name,
                        len(collected),
                    )
                    break

            normalised = [
                n for raw in result
                if (n := self._normalise(raw, app_name)) is not None
            ]
            collected.extend(normalised)

            logger.info("Collected %d reviews for %s so far.", len(collected), app_name)

            if continuation_token is None:
                break

            time.sleep(_SLEEP_BETWEEN_PAGES)

        return collected

    @staticmethod
    def _normalise(raw: dict, app_name: str) -> dict | None:
        """Convert a raw google-play-scraper dict to the reviews table schema.

        Never mutates the input dict — always returns a new object.

        Args:
            raw: Raw dict returned by google-play-scraper.
            app_name: App name to embed in the returned dict.

        Returns:
            New dict with keys matching the reviews table columns.
        """
        review_id = raw.get("reviewId", "")
        if not review_id:
            return None  # H1: skip reviews with missing/empty reviewId

        at_value = raw.get("at")
        date_str = at_value.isoformat() if isinstance(at_value, datetime) else str(at_value or "")

        reply_content = raw.get("replyContent")
        has_dev_reply = 1 if reply_content is not None else 0

        return {
            "app_name": app_name,
            "review_id": review_id,
            "rating": raw.get("score", 0),
            "text": raw.get("content", ""),
            "date": date_str,
            "thumbs_up": raw.get("thumbsUpCount", 0),
            "has_dev_reply": has_dev_reply,
            "dev_reply_text": reply_content,
            "scraped_at": datetime.now(timezone.utc).isoformat(),  # M7: UTC
            "classification": None,
        }
