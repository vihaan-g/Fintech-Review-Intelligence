"""Wraps a single LLM council member and its API call logic."""
import logging

from src.config import Config

logger = logging.getLogger(__name__)


class CouncilMember:
    """Represents one LLM participant in the 3-stage council."""

    def __init__(
        self,
        name: str,
        provider: str,
        model_id: str,
        config: Config,
    ) -> None:
        """Initialise a council member. Extracts API key from config based on provider.

        provider must be one of: 'gemini', 'openrouter'

        Args:
            name: Human-readable label (e.g. 'deepseek-r1').
            provider: API provider — 'gemini' or 'openrouter'.
            model_id: API model identifier string.
            config: Validated Config instance.
        """
        self.name = name
        self.provider = provider
        self.model_id = model_id
        self._api_key = (
            config.gemini_api_key
            if provider == "gemini"
            else config.openrouter_api_key
        )

    def generate(self, prompt: str) -> str:
        """Send a prompt and return the model's text response."""
        raise NotImplementedError
