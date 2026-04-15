"""Wraps a single LLM council member and its API call logic."""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class CouncilMember:
    """Represents one LLM participant in the 3-stage council."""

    def __init__(
        self,
        name: str,
        model_id: str,
        api_key: str,
        base_url: str,
        timeout: float = 60.0,
    ) -> None:
        """Initialize a council member.

        Args:
            name: Human-readable label (e.g. 'deepseek-r1').
            model_id: API model identifier string.
            api_key: Authentication key for the model's API.
            base_url: Base URL for the model's HTTP endpoint.
            timeout: Request timeout in seconds.
        """
        self.name = name
        self.model_id = model_id
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    def generate(self, prompt: str) -> str:
        """Send a prompt and return the model's text response."""
        raise NotImplementedError
