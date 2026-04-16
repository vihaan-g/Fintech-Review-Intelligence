"""Processes reviews in batches respecting API rate limits."""
import logging

from src.classification.review_classifier import ReviewClassifier

logger = logging.getLogger(__name__)


class BatchProcessor:
    """Iterates over unclassified reviews and calls ReviewClassifier in batches."""

    def __init__(
        self,
        classifier: ReviewClassifier,
        batch_size: int = 50,
        sleep_seconds: float = 1.0,
    ) -> None:
        """Initialize with a classifier and batching parameters.

        Args:
            classifier: A ReviewClassifier instance.
            batch_size: Number of reviews per batch.
            sleep_seconds: Delay between batches to avoid rate limits.
        """
        self.classifier = classifier
        self.batch_size = batch_size
        self.sleep_seconds = sleep_seconds

    def process(self, reviews: list[dict]) -> list[dict]:
        """Classify all reviews and return them with classification fields added."""
        raise NotImplementedError
