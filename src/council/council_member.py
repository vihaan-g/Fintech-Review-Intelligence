"""Represents a single LLM council member and its API call logic."""
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from src.config import Config

logger = logging.getLogger(__name__)

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)
_OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


@dataclass
class MemberResponse:
    """Response from a single council member."""

    member_name: str
    model_id: str
    raw_response: str
    clean_response: str  # think tags stripped
    timestamp: str       # ISO format
    duration_ms: int


class CouncilMember:
    """Represents one LLM in the council. Handles its own API calls.

    Supports two providers: 'gemini' (Google AI Studio) and
    'openrouter' (OpenRouter unified API for DeepSeek, Qwen3, Llama 4).
    Strips <think>...</think> blocks from responses before returning.
    """

    def __init__(
        self,
        name: str,
        provider: str,
        model_id: str,
        config: Config,
    ) -> None:
        """Initialise council member.

        Args:
            name:     Human-readable name (e.g. 'DeepSeek R1')
            provider: 'gemini' or 'openrouter'
            model_id: Full model string (e.g. 'deepseek/deepseek-r1:free')
            config:   Config instance — extracts API key by provider
        """
        self.name = name
        self.provider = provider
        self.model_id = model_id
        self._api_key = (
            config.gemini_api_key
            if provider == "gemini"
            else config.openrouter_api_key
        )

    async def generate(self, prompt: str) -> MemberResponse:
        """Send prompt to this member's model and return MemberResponse.

        Uses httpx.AsyncClient with a 90s timeout (Qwen3 can be slow).
        Strips <think>...</think> from response before setting clean_response.
        Never raises — on any error returns MemberResponse with
        clean_response = "" and raw_response = str(exception).
        """
        start = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            if self.provider == "gemini":
                raw = await self._call_gemini(prompt)
            elif self.provider == "openrouter":
                raw = await self._call_openrouter(prompt)
            else:
                raise ValueError(f"Unknown provider: {self.provider}")
            clean = self._strip_think_tags(raw)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CouncilMember %s error: %s", self.name, exc)
            raw = str(exc)
            clean = ""
        duration_ms = int((time.monotonic() - start) * 1000)
        return MemberResponse(
            member_name=self.name,
            model_id=self.model_id,
            raw_response=raw,
            clean_response=clean,
            timestamp=timestamp,
            duration_ms=duration_ms,
        )

    def _strip_think_tags(self, text: str) -> str:
        """Remove <think>...</think> blocks including content.

        Uses re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).
        Returns stripped text.
        """
        return _THINK_RE.sub("", text).strip()

    async def _call_gemini(self, prompt: str) -> str:
        """POST to Gemini 2.5 Flash via Google AI Studio.

        Endpoint:
            https://generativelanguage.googleapis.com/v1beta/models/
            gemini-2.5-flash:generateContent?key={api_key}
        Body: {"contents": [{"parts": [{"text": prompt}]}]}
        Timeout: 90s. Returns text string.
        """
        url = f"{_GEMINI_ENDPOINT}?key={self._api_key}"
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
        return str(data["candidates"][0]["content"]["parts"][0]["text"])

    async def _call_openrouter(self, prompt: str) -> str:
        """POST to OpenRouter via OpenAI-compatible chat completions.

        Endpoint: https://openrouter.ai/api/v1/chat/completions
        Headers:
            Authorization: Bearer {openrouter_api_key}
            HTTP-Referer: https://github.com/vihaan-g/fintech-review-intelligence
            X-Title: Fintech Review Intelligence
        Body:
            {
              "model": "{model_id}",
              "messages": [{"role": "user", "content": prompt}]
            }
        Timeout: 90s. Returns content string from choices[0].message.content.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://github.com/vihaan-g/fintech-review-intelligence",
            "X-Title": "Fintech Review Intelligence",
        }
        body = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                _OPENROUTER_ENDPOINT, json=body, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
        return str(data["choices"][0]["message"]["content"])
