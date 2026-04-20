"""Configuration management: validates required environment variables at startup."""
import logging
import os
import sys


class Config:
    """Validates and exposes all required environment variables at startup.

    Raises ValueError immediately on init if any required key is missing,
    so pipeline failures happen at startup, not mid-run.
    """

    def __init__(
        self,
        openrouter_api_key: str,
    ) -> None:
        """Store validated API keys.

        Args:
            openrouter_api_key: OpenRouter API key for all LLM requests.
        """
        self.openrouter_api_key = openrouter_api_key

    @property
    def gemini_api_key(self) -> str:
        """Compatibility alias during the OpenRouter-only migration.

        Existing classification/council code still reads ``config.gemini_api_key``.
        Returning the OpenRouter key keeps Phase B non-breaking while later phases
        migrate those call sites to the canonical ``openrouter_api_key`` field.
        """
        return self.openrouter_api_key

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
            openrouter_api_key=values["OPENROUTER_API_KEY"],
        )

    @staticmethod
    def setup_logging(level: int = logging.INFO) -> None:
        """Configure root logger with colour output on TTYs."""
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(_ColourFormatter())
        logging.basicConfig(level=level, handlers=[handler])


class _ColourFormatter(logging.Formatter):
    """Adds ANSI colour to log level names when writing to a TTY."""

    _GREY   = "\x1b[38;5;245m"
    _CYAN   = "\x1b[36m"
    _YELLOW = "\x1b[33m"
    _RED    = "\x1b[31m"
    _BOLD_RED = "\x1b[1;31m"
    _RESET  = "\x1b[0m"

    _COLOURS = {
        logging.DEBUG:    _GREY,
        logging.INFO:     _CYAN,
        logging.WARNING:  _YELLOW,
        logging.ERROR:    _RED,
        logging.CRITICAL: _BOLD_RED,
    }

    _FMT = "%(asctime)s  {colour}%(levelname)-8s{reset}  %(name)s  %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        use_colour = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        colour = self._COLOURS.get(record.levelno, "")
        fmt = self._FMT.format(
            colour=colour if use_colour else "",
            reset=self._RESET if use_colour else "",
        )
        formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")
        return formatter.format(record)
