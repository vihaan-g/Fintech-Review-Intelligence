"""Scrapes Play Store reviews for target fintech apps."""
import logging
from typing import List

logger = logging.getLogger(__name__)


class ReviewCollector:
    """Fetches Play Store reviews for a given app using google-play-scraper."""

    def __init__(self, app_id: str, count: int = 2000) -> None:
        """Initialize collector for a specific app.

        Args:
            app_id: Play Store package ID (e.g. 'com.fimoney.app').
            count: Number of reviews to fetch per app.
        """
        self.app_id = app_id
        self.count = count

    def fetch(self) -> List[dict]:
        """Fetch reviews from Play Store and return as a list of dicts."""
        raise NotImplementedError
