"""Represents a single LLM council member and its API call logic."""
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from src.config import Config

logger = logging.getLogger(__name__)

_GEMINI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model_id}:generateContent"
)
_OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

_TIMEOUT_GEMINI = 30.0
_TIMEOUT_OPENROUTER = 90.0  # Qwen3-235B can be slow


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

    Fatal HTTP 4xx errors (bad model ID, auth failure) are re-raised from
    generate() so the orchestrator can surface them clearly — they are not
    swallowed and silently converted to empty responses.
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

        Creates one AsyncClient per generate() call with per-provider timeout
        (30s for Gemini, 90s for OpenRouter). The client is shared across any
        internal retries that _call_gemini / _call_openrouter may perform.

        Fatal HTTP 4xx errors propagate (bad model ID → 404, auth → 401/403,
        bad request → 400). All other errors are logged and return empty response.

        Strips <think>...</think> from response before setting clean_response.
        """
        timeout = _TIMEOUT_GEMINI if self.provider == "gemini" else _TIMEOUT_OPENROUTER
        start = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        raw = ""
        clean = ""
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if self.provider == "gemini":
                    raw = await self._call_gemini(prompt, client)
                elif self.provider == "openrouter":
                    raw = await self._call_openrouter(prompt, client)
                else:
                    raise ValueError(f"Unknown provider: {self.provider}")
            clean = self._strip_think_tags(raw)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if 400 <= status < 500:
                # Fatal: wrong model ID (404), bad auth (401/403), bad request (400).
                # Propagate so the orchestrator surfaces the failure clearly.
                logger.error(
                    "CouncilMember %s — fatal HTTP %d, not retrying",
                    self.name, status,
                )
                raise
            logger.warning("CouncilMember %s HTTP error: %s", self.name, exc)
            raw = str(exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("CouncilMember %s error: %s", self.name, exc)
            raw = str(exc)
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
        """Remove <think>...</think> blocks including content."""
        return _THINK_RE.sub("", text).strip()

    async def _call_gemini(self, prompt: str, client: httpx.AsyncClient) -> str:
        """POST to Gemini via Google AI Studio for any Gemini model.

        Uses the provided AsyncClient (created once per generate() call).
        Adds generationConfig with temperature=0.3 for consistent council output.
        Checks for safety filtering and missing candidates before indexing.

        Args:
            prompt: The text prompt to send.
            client: Shared AsyncClient from generate().

        Returns:
            Model response text. Empty string if safety-filtered.
        """
        url = _GEMINI_ENDPOINT_TEMPLATE.format(model_id=self.model_id)
        headers = {"x-goog-api-key": self._api_key}
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3},
        }
        resp = await client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        # Prompt-level block (before any candidate is generated)
        block_reason = data.get("promptFeedback", {}).get("blockReason")
        if block_reason:
            logger.warning(
                "Gemini blocked prompt for %s (blockReason=%s)",
                self.model_id, block_reason,
            )
            return ""

        candidates = data.get("candidates", [])
        if not candidates:
            logger.warning("Gemini returned no candidates for model %s", self.model_id)
            return ""

        candidate = candidates[0]
        finish_reason = candidate.get("finishReason", "STOP")
        if finish_reason == "SAFETY":
            logger.warning(
                "Gemini safety filter triggered for model %s — returning empty",
                self.model_id,
            )
            return ""

        return str(candidate["content"]["parts"][0]["text"])

    async def _call_openrouter(self, prompt: str, client: httpx.AsyncClient) -> str:
        """POST to OpenRouter via OpenAI-compatible chat completions.

        Args:
            prompt: The text prompt to send.
            client: Shared AsyncClient from generate().

        Returns:
            Model response text from choices[0].message.content.
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
        resp = await client.post(_OPENROUTER_ENDPOINT, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return str(data["choices"][0]["message"]["content"])
