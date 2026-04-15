"""Configuration management: validates required environment variables at startup."""
import os


class Config:
    """Validates and exposes all required environment variables for the pipeline."""

    def __init__(self) -> None:
        """Load and validate all required environment variables."""
        self.gemini_api_key: str = self._require("GEMINI_API_KEY")
        self.openrouter_api_key: str = self._require("OPENROUTER_API_KEY")

    def _require(self, key: str) -> str:
        """Return the value of an environment variable or raise ValueError."""
        value = os.getenv(key)
        if not value:
            raise ValueError(
                f"Required environment variable '{key}' is missing or empty. "
                "Check your .env file."
            )
        return value
