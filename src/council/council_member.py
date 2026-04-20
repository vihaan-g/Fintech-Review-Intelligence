"""Represents a single LLM council member and its API call logic."""
import asyncio
import logging
import random
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

_TIMEOUT_GEMINI = 90.0
_TIMEOUT_OPENROUTER = 90.0  # Qwen3-235B can be slow

_MAX_RETRIES = 3
_RETRYABLE_STATUS_CODES = frozenset([408, 429, 500, 502, 503, 504])
_BACKOFF_BASE_SECONDS = 5.0


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
            model_id: Full model string (e.g. 'deepseek/deepseek-r1')
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
        (90s for Gemini, 90s for OpenRouter). The client is shared across any
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
            if 400 <= status < 500 and status != 429:
                # Fatal: wrong model ID (404), bad auth (401/403), bad request (400).
                # Propagate so the orchestrator surfaces the failure clearly.
                # Note: 429 is retryable inside _post_with_retries; if it escapes
                # here, all retries are exhausted — treat as non-fatal empty.
                body_snippet = ""
                try:
                    body_snippet = exc.response.text[:200]
                except Exception:  # noqa: BLE001
                    pass
                logger.error(
                    "CouncilMember %s — fatal HTTP %d, not retrying. Body: %s",
                    self.name, status, body_snippet,
                )
                raise
            logger.warning(
                "CouncilMember %s HTTP %s after retries: %s",
                self.name, status, exc,
            )
            raw = f"[error: HTTP {status} after retries] {exc}"
        except httpx.TransportError as exc:
            logger.warning(
                "CouncilMember %s transport error (%s) after retries: %s",
                self.name, type(exc).__name__, exc,
            )
            raw = f"[error: {type(exc).__name__} after retries] {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CouncilMember %s unexpected error (%s): %s",
                self.name, type(exc).__name__, exc,
            )
            raw = f"[error: {type(exc).__name__}] {exc}"
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

    async def _post_with_retries(
        self,
        client: httpx.AsyncClient,
        url: str,
        json_body: dict,
        headers: dict,
    ) -> httpx.Response:
        """POST with bounded retry on transient failures.

        Retries on:
          - httpx.TransportError (ConnectError, ReadTimeout, RemoteProtocolError,
            PoolTimeout, ReadError, WriteError, LocalProtocolError, etc.)
          - Retryable HTTP status codes (429, 500, 502, 503, 504)

        Non-retryable 4xx (400/401/403/404) propagate immediately via
        raise_for_status() so the orchestrator can surface bad-key / bad-model
        failures without burning 2 minutes of backoff first.
        """
        last_exc: Exception = RuntimeError("All retries exhausted")
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await client.post(url, json=json_body, headers=headers)
                if resp.status_code in _RETRYABLE_STATUS_CODES:
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}",
                        request=resp.request, response=resp,
                    )
                    logger.warning(
                        "CouncilMember %s retryable HTTP %d on attempt %d/%d",
                        self.name, resp.status_code, attempt + 1, _MAX_RETRIES,
                    )
                else:
                    # Fatal 4xx will raise here; 2xx returns the response.
                    resp.raise_for_status()
                    return resp
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "CouncilMember %s network error (%s) on attempt %d/%d: %s",
                    self.name, type(exc).__name__, attempt + 1, _MAX_RETRIES, exc,
                )

            if attempt < _MAX_RETRIES - 1:
                delay = _BACKOFF_BASE_SECONDS * (2 ** attempt) + random.uniform(0, 1.5)
                logger.info(
                    "CouncilMember %s sleeping %.1fs before retry %d/%d",
                    self.name, delay, attempt + 2, _MAX_RETRIES,
                )
                await asyncio.sleep(delay)

        raise last_exc

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
        resp = await self._post_with_retries(client, url, body, headers)
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

        # Defensive extraction: response shape varies between Gemini 2.5 and
        # Gemini 3 (and across "thinking" vs non-thinking variants). Treat any
        # missing field as empty rather than raising KeyError or producing the
        # literal string "None" downstream.
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        if not parts:
            logger.warning(
                "Gemini returned candidate with no parts for model %s "
                "(finishReason=%s) — returning empty",
                self.model_id, finish_reason,
            )
            return ""
        text = parts[0].get("text")
        if not text:
            logger.warning(
                "Gemini candidate part has no text for model %s "
                "(finishReason=%s) — returning empty",
                self.model_id, finish_reason,
            )
            return ""
        return str(text)

    async def _call_openrouter(self, prompt: str, client: httpx.AsyncClient) -> str:
        """POST to OpenRouter via OpenAI-compatible chat completions.

        Args:
            prompt: The text prompt to send.
            client: Shared AsyncClient from generate().

        Returns:
            Model response text from choices[0].message.content. Falls back to
            ``message.reasoning`` when content is null (some upstream
            providers — e.g. Z.AI GLM, certain Nemotron variants — emit the
            user-facing answer in the OpenRouter `reasoning` field with
            ``content: null`` when thinking mode is on). Returns empty string
            on any malformed / refused response rather than producing the
            literal string "None" downstream.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://github.com/vihaan-g/fintech-review-intelligence",
            "X-OpenRouter-Title": "Fintech Review Intelligence",
        }
        body = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
        }
        resp = await self._post_with_retries(client, _OPENROUTER_ENDPOINT, body, headers)
        data = resp.json()

        # Some OpenRouter providers wrap upstream errors in a 200 response with
        # a top-level "error" key — surface those instead of indexing into a
        # missing "choices" array.
        upstream_error = data.get("error")
        if upstream_error:
            logger.warning(
                "OpenRouter %s returned upstream error: %s",
                self.model_id, upstream_error,
            )
            return ""

        choices = data.get("choices") or []
        if not choices:
            logger.warning(
                "OpenRouter %s returned no choices — returning empty",
                self.model_id,
            )
            return ""

        message = choices[0].get("message") or {}
        content = message.get("content")
        if content:
            return str(content)

        # content is None or empty — providers with thinking mode (GLM,
        # Nemotron, etc.) sometimes route the answer through "reasoning".
        reasoning = message.get("reasoning")
        if reasoning:
            logger.info(
                "OpenRouter %s returned null content; using reasoning field",
                self.model_id,
            )
            return str(reasoning)

        logger.warning(
            "OpenRouter %s returned message with no content or reasoning "
            "(finish_reason=%s) — returning empty",
            self.model_id, choices[0].get("finish_reason"),
        )
        return ""
