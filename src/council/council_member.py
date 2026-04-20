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

_OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

_TIMEOUT_OPENROUTER = 120.0
_MAX_RETRIES = 3
_RETRYABLE_STATUS_CODES = frozenset([408, 429, 500, 502, 503, 504])
_BACKOFF_BASE_SECONDS = 5.0


@dataclass
class MemberResponse:
    """Response from a single council member."""

    member_name: str
    model_id: str
    raw_response: str
    clean_response: str
    timestamp: str
    duration_ms: int


class CouncilMember:
    """Represents one council model and handles its OpenRouter API calls."""

    def __init__(
        self,
        name: str,
        provider: str,
        model_id: str,
        config: Config,
    ) -> None:
        """Initialise a council member.

        The ``provider`` argument is retained for constructor compatibility, but
        council calls now run through OpenRouter only.
        """
        self.name = name
        self.provider = "openrouter"
        self.model_id = model_id
        self._api_key = config.openrouter_api_key

    async def generate(self, prompt: str) -> MemberResponse:
        """Send a prompt to this member and return a cleaned response."""
        start = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()
        raw = ""
        clean = ""
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT_OPENROUTER) as client:
                raw = await self._call_openrouter(prompt, client)
            clean = self._strip_think_tags(raw)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if 400 <= status < 500 and status != 429:
                body_snippet = exc.response.text[:200]
                logger.error(
                    "CouncilMember %s fatal HTTP %d. Body: %s",
                    self.name,
                    status,
                    body_snippet,
                )
                raise
            logger.warning(
                "CouncilMember %s HTTP %d after retries: %s",
                self.name,
                status,
                exc,
            )
            raw = f"[error: HTTP {status} after retries] {exc}"
        except httpx.TransportError as exc:
            logger.warning(
                "CouncilMember %s transport error (%s) after retries: %s",
                self.name,
                type(exc).__name__,
                exc,
            )
            raw = f"[error: {type(exc).__name__} after retries] {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "CouncilMember %s unexpected error (%s): %s",
                self.name,
                type(exc).__name__,
                exc,
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
        """Remove provider reasoning tags from the visible response."""
        return _THINK_RE.sub("", text).strip()

    async def _post_with_retries(
        self,
        client: httpx.AsyncClient,
        json_body: dict,
        headers: dict,
    ) -> httpx.Response:
        """POST to OpenRouter with bounded retries on transient failures."""
        last_exc: Exception = RuntimeError("All retries exhausted")
        for attempt in range(_MAX_RETRIES):
            try:
                response = await client.post(
                    _OPENROUTER_ENDPOINT,
                    json=json_body,
                    headers=headers,
                )
                if response.status_code in _RETRYABLE_STATUS_CODES:
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    logger.warning(
                        "CouncilMember %s retryable HTTP %d on attempt %d/%d",
                        self.name,
                        response.status_code,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                else:
                    response.raise_for_status()
                    return response
            except httpx.TransportError as exc:
                last_exc = exc
                logger.warning(
                    "CouncilMember %s network error (%s) on attempt %d/%d: %s",
                    self.name,
                    type(exc).__name__,
                    attempt + 1,
                    _MAX_RETRIES,
                    exc,
                )

            if attempt < _MAX_RETRIES - 1:
                delay = _BACKOFF_BASE_SECONDS * (2**attempt) + random.uniform(0, 1.5)
                logger.info(
                    "CouncilMember %s sleeping %.1fs before retry %d/%d",
                    self.name,
                    delay,
                    attempt + 2,
                    _MAX_RETRIES,
                )
                await asyncio.sleep(delay)

        raise last_exc

    async def _call_openrouter(self, prompt: str, client: httpx.AsyncClient) -> str:
        """POST to OpenRouter via OpenAI-compatible chat completions."""
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://github.com/vihaan-g/fintech-review-intelligence",
            "X-OpenRouter-Title": "Fintech Review Intelligence",
        }
        body = {
            "model": self.model_id,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
        }
        response = await self._post_with_retries(client, body, headers)
        data = response.json()

        upstream_error = data.get("error")
        if upstream_error:
            logger.warning(
                "OpenRouter %s returned upstream error: %s",
                self.model_id,
                upstream_error,
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
            self.model_id,
            choices[0].get("finish_reason"),
        )
        return ""
