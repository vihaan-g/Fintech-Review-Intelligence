"""Classifies individual reviews using Gemini 2.5 Flash."""
import logging

from src.config import Config

logger = logging.getLogger(__name__)


class ReviewClassifier:
    """Sends a review to Gemini and returns a structured classification."""

    def __init__(self, config: Config, model: str = "gemini-2.5-flash") -> None:
        """Initialise classifier. Extracts Gemini API key from config.

        Args:
            config: Validated Config instance.
            model: Gemini model identifier.
        """
        self._api_key = config.gemini_api_key
        self.model = model

    def classify(self, review_text: str) -> dict[str, str]:
        """Classify a single review and return a label dict."""
        raise NotImplementedError
