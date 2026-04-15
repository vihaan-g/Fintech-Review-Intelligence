"""Configuration management: validates required environment variables at startup."""
import logging
import os


class Config:
    """Validates and exposes all required environment variables at startup.

    Raises ValueError immediately on init if any required key is missing,
    so pipeline failures happen at startup, not mid-run.
    """

    def __init__(
        self,
        gemini_api_key: str,
        openrouter_api_key: str,
    ) -> None:
        """Store validated API keys.

        Args:
            gemini_api_key: Google AI Studio API key for Gemini.
            openrouter_api_key: OpenRouter API key for council members.
        """
        self.gemini_api_key = gemini_api_key
        self.openrouter_api_key = openrouter_api_key

    @classmethod
    def from_env(cls) -> "Config":
        """Load config from environment variables. Raises ValueError if any are missing.

        Collects ALL missing keys before raising so the caller sees the full
        list of absent variables in a single error message.

        Returns:
            Config: A fully-populated Config instance.

        Raises:
            ValueError: If one or more required environment variables are absent.
        """
        key_map: dict[str, str] = {
            "GEMINI_API_KEY": "",
            "OPENROUTER_API_KEY": "",
        }

        missing: list[str] = []
        values: dict[str, str] = {}

        for env_var in key_map:
            value = os.getenv(env_var)
            if not value:
                missing.append(env_var)
            else:
                values[env_var] = value

        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        return cls(
            gemini_api_key=values["GEMINI_API_KEY"],
            openrouter_api_key=values["OPENROUTER_API_KEY"],
        )

    @staticmethod
    def setup_logging(level: int = logging.INFO) -> None:
        """Configure root logger. Call once at pipeline startup."""
        logging.basicConfig(
            level=level,
            format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
