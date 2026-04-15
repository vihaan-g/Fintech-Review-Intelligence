"""Classifies individual reviews using Gemini 2.5 Flash."""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class ReviewClassifier:
    """Sends a review to Gemini and returns a structured classification."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash") -> None:
        """Initialize with Gemini credentials.

        Args:
            api_key: Google AI Studio API key.
            model: Gemini model identifier.
        """
        self.api_key = api_key
        self.model = model

    def classify(self, review_text: str) -> Dict[str, str]:
        """Classify a single review and return a label dict."""
        raise NotImplementedError
