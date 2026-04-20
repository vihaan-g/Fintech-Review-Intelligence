"""Classifies Play Store reviews via OpenRouter."""
import json
import logging
import random
import re
import time
from dataclasses import dataclass

import httpx

from src.config import Config

_OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
_CLASSIFICATION_MODEL = "google/gemini-2.5-flash-lite"
_MAX_RETRIES = 5
_RETRYABLE_STATUS_CODES = frozenset([429, 500, 502, 503])
_REQUEST_TIMEOUT_SECONDS = 30.0
_BACKOFF_BASE_SECONDS = 10.0


class OpenRouterRateLimitError(RuntimeError):
    """Raised when OpenRouter returns 429 after all retries are exhausted.

    Signals a durable rate-limit or account-level limit condition.
    BatchProcessor catches this, checkpoints progress, and exits cleanly
    so the run can resume on the next invocation.
    """


class OpenRouterAuthError(RuntimeError):
    """Raised when OpenRouter returns 400/401/403 — fatal auth/config failure.

    Never caught by classify_batch; always propagates to the caller so a bad
    key is surfaced immediately rather than producing 10k silent parse failures.
    """


class OpenRouterNetworkError(RuntimeError):
    """Raised when all retries are exhausted due to network/transport errors.

    Signals a transient connectivity failure (ConnectError, TimeoutException,
    etc.) rather than an auth issue. BatchProcessor catches this,
    checkpoints progress, and exits so the run can be retried.
    """


@dataclass
class ClassificationResult:
    """Result of classifying a single review."""

    product_area: str
    specific_feature_request: str | None
    workflow_breakdown: bool
    confidence: float
    raw_response: str
    parse_failed: bool = False


class ReviewClassifier:
    """Classifies Play Store reviews using Gemini 2.5 Flash Lite via OpenRouter.

    Uses prompt-optimizer output for classification prompts.
    Never raises on parse failure — returns low-confidence result instead.
    Auth errors (OpenRouterAuthError) propagate immediately without swallowing.
    All API calls go through _call_openrouter() for testability.
    """

    SINGLE_REVIEW_PROMPT: str = (
        "You are a classification engine for Play Store reviews of Indian fintech apps "
        "(Groww, Jupiter, CRED, PhonePe, Paytm).\n\n"
        "TASK: Classify the review below into exactly one product area and extract a "
        "specific feature request if and only if one is explicitly stated in the review text.\n\n"
        "PRODUCT AREAS (choose exactly one):\n"
        "- onboarding   \u2014 account setup, KYC, sign-up, first-use friction\n"
        "- ux           \u2014 navigation, UI design, confusing flows, app usability\n"
        "- transactions \u2014 payments, UPI, transfers, bill pay, recharges\n"
        "- support      \u2014 customer care, chat support, complaint resolution\n"
        "- performance  \u2014 crashes, slowness, bugs, error messages, login failures\n"
        "- trust        \u2014 security, fraud, scam, data privacy, verification failures\n"
        "- other        \u2014 unrelated to the app (phone issues, wrong app, etc.)\n\n"
        "RULES:\n"
        "1. product_area MUST be one of the seven values listed \u2014 no other values are valid\n"
        "2. specific_feature_request: extract verbatim only if the reviewer explicitly names "
        "a feature they want; null if no feature request is stated \u2014 do not invent or infer\n"
        "3. workflow_breakdown: true ONLY when the reviewer describes a specific failed sequence "
        '("tried X \u2192 Y failed \u2192 result was Z"); false for vague complaints\n'
        "4. confidence: 0.9\u20131.0 only when the product_area is unambiguous; 0.5\u20130.85 "
        "when two areas apply nearly equally; never 1.0 for edge cases\n\n"
        "REVIEW:\n"
        "{review_text}\n\n"
        "Return a single JSON object. No preamble. No markdown fences. No explanation. "
        'All four fields required.\n\n'
        '{{"product_area": "<value>", "specific_feature_request": "<text or null>", '
        '"workflow_breakdown": true, "confidence": 0.0}}'
    )

    BATCH_CLASSIFICATION_PROMPT: str = (
        "You are a classification engine for Play Store reviews of Indian fintech apps "
        "(Groww, Jupiter, CRED, PhonePe, Paytm).\n\n"
        "TASK: Classify each review in the numbered list below. Return a JSON array of "
        "the same length in the same order.\n\n"
        "PRODUCT AREAS (choose exactly one per review):\n"
        "- onboarding   \u2014 account setup, KYC, sign-up, first-use friction\n"
        "- ux           \u2014 navigation, UI design, confusing flows, app usability\n"
        "- transactions \u2014 payments, UPI, transfers, bill pay, recharges\n"
        "- support      \u2014 customer care, chat support, complaint resolution\n"
        "- performance  \u2014 crashes, slowness, bugs, error messages, login failures\n"
        "- trust        \u2014 security, fraud, scam, data privacy, verification failures\n\n"
        "RULES (apply independently to each review):\n"
        "1. product_area MUST be one of the six values listed \u2014 no other values are valid\n"
        "2. specific_feature_request: extract verbatim only if the reviewer explicitly names "
        "a feature they want; null if no feature request is stated \u2014 do not invent or infer\n"
        "3. workflow_breakdown: true ONLY when the reviewer describes a specific failed sequence "
        '("tried X \u2192 Y failed \u2192 result was Z"); false for vague complaints\n'
        "4. confidence: 0.9\u20131.0 only when the product_area is unambiguous; 0.5\u20130.85 "
        "when two areas apply nearly equally; never 1.0 for edge cases\n"
        "5. Classify each review INDEPENDENTLY \u2014 prior reviews must not influence later ones\n\n"
        "REVIEWS:\n"
        "{reviews_json}\n\n"
        "Return a JSON array of exactly {batch_size} objects in the same order as the input. "
        "No preamble. No markdown fences. No explanation. Every object must have all four fields."
    )

    VALID_PRODUCT_AREAS: frozenset[str] = frozenset([
        "onboarding", "ux", "transactions",
        "support", "performance", "trust", "other",
    ])

    def __init__(self, config: Config) -> None:
        """Initialise classifier. Extracts OpenRouter API key from config."""
        self._api_key: str = config.openrouter_api_key
        self._logger = logging.getLogger(self.__class__.__name__)
        # Tracks whether any request in this run has returned 200. Enables
        # fast-fail when the very first request of a resumed run hits 429 —
        # a signal that the account is still rate-limited or has no available
        # capacity. Without this we burn ~150s on retry backoff before giving up.
        self._has_succeeded: bool = False

    def classify_batch(self, reviews: list[dict]) -> list[ClassificationResult]:
        """Classify a batch of reviews in a single OpenRouter API call.

        Sends all reviews as a JSON array in one prompt.
        Expects a JSON array response of the same length.
        On parse failure: returns list of parse_failed=True results for the batch.
        OpenRouterRateLimitError and OpenRouterAuthError always propagate.
        Never raises for other errors. Always returns same length as input.

        Args:
            reviews: list of review dicts with at minimum a 'text' field

        Returns:
            list of ClassificationResult, one per input review, same order
        """
        if not reviews:
            return []

        prompt = self._build_batch_prompt(reviews)
        try:
            raw = self._call_openrouter(prompt)
        except OpenRouterRateLimitError:
            raise
        except OpenRouterAuthError:
            raise  # fatal — surfaces bad key immediately, never silent
        except OpenRouterNetworkError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._logger.warning(
                "OpenRouter API call failed for batch of %d: %s", len(reviews), exc
            )
            return [self._make_parse_failed_result() for _ in reviews]

        return self._parse_batch_response(raw, batch_size=len(reviews))

    def _call_openrouter(self, prompt: str) -> str:
        """Make a single synchronous HTTP POST to OpenRouter chat completions.

        Returns the model's text response as a raw string.
        Raises OpenRouterAuthError on 400/401/403 (fatal, no retry).
        Raises OpenRouterRateLimitError when all retries see 429.
        Raises httpx.HTTPStatusError for other non-retryable status codes.
        Timeout: 30 seconds.
        """
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": "https://github.com/vihaan-g/fintech-review-intelligence",
            "X-OpenRouter-Title": "Fintech Review Intelligence",
        }
        body = {
            "model": _CLASSIFICATION_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
        last_exc: Exception = RuntimeError("All retries exhausted")
        saw_429 = False

        for attempt in range(_MAX_RETRIES):
            try:
                response = httpx.post(
                    _OPENROUTER_ENDPOINT,
                    json=body,
                    headers=headers,
                    timeout=_REQUEST_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()

                upstream_error = data.get("error")
                if upstream_error:
                    self._logger.warning(
                        "OpenRouter returned upstream error for classification: %s",
                        upstream_error,
                    )
                    return ""

                choices = data.get("choices") or []
                if not choices:
                    self._logger.warning("OpenRouter returned no choices")
                    return ""

                message = choices[0].get("message") or {}
                content = message.get("content")
                if not content:
                    self._logger.warning(
                        "OpenRouter classification response has no content "
                        "(finish_reason=%s) — returning empty",
                        choices[0].get("finish_reason"),
                    )
                    return ""
                self._has_succeeded = True
                return str(content)

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status in {400, 401, 403}:
                    raise OpenRouterAuthError(
                        f"OpenRouter auth/request error HTTP {status} — check OPENROUTER_API_KEY. "
                        f"Body: {exc.response.text[:200]}"
                    ) from exc
                if status == 429:
                    saw_429 = True
                    # Fast-fail: if we haven't had a single successful call in
                    # this run, a 429 on the first attempt means the daily
                    # limit or account capacity issue is still active. Retrying
                    # 4 more times with exponential backoff (~150s total) is
                    # pure waste — raise immediately so the operator sees a
                    # clean message and can come back after limits clear.
                    if not self._has_succeeded and attempt == 0:
                        raise OpenRouterRateLimitError(
                            "OpenRouter returned 429 on the very first request — "
                            "the account is still rate-limited or unavailable. "
                            "Wait and re-run to resume from checkpoint."
                        ) from exc
                if status not in _RETRYABLE_STATUS_CODES:
                    raise
                last_exc = exc
                self._logger.warning(
                    "Retryable HTTP %d on attempt %d/%d",
                    status, attempt + 1, _MAX_RETRIES,
                )
            except httpx.TransportError as exc:
                # TransportError covers TimeoutException, NetworkError
                # (ConnectError, ReadError, WriteError, CloseError), ProtocolError
                # (RemoteProtocolError, LocalProtocolError), ProxyError, PoolTimeout.
                # Without this, a mid-run network blip would propagate to
                # classify_batch and mark the whole batch parse_failed instead of
                # retrying through the same path as 5xx responses.
                last_exc = exc
                self._logger.warning(
                    "Network error (%s) on attempt %d/%d: %s",
                    type(exc).__name__, attempt + 1, _MAX_RETRIES, exc,
                )

            if attempt < _MAX_RETRIES - 1:
                retry_after_header = (
                    last_exc.response.headers.get("Retry-After")
                    if isinstance(last_exc, httpx.HTTPStatusError)
                    else None
                )
                base_delay = _BACKOFF_BASE_SECONDS * (2 ** attempt)
                jitter = random.uniform(0, 2)
                delay = base_delay + jitter
                if retry_after_header:
                    try:
                        delay = max(delay, float(retry_after_header))
                    except ValueError:
                        pass
                self._logger.info(
                    "Sleeping %.1fs before retry %d/%d",
                    delay, attempt + 2, _MAX_RETRIES,
                )
                time.sleep(delay)

        if saw_429:
            raise OpenRouterRateLimitError(
                "OpenRouter returned 429 on every retry — classification progress "
                "has been checkpointed; re-run to resume."
            )
        if isinstance(last_exc, httpx.TransportError):
            raise OpenRouterNetworkError(
                f"Network error after {_MAX_RETRIES} retries — {last_exc}"
            ) from last_exc
        raise last_exc

    def _build_batch_prompt(self, reviews: list[dict]) -> str:
        """Build the batch classification prompt for a list of reviews."""
        numbered = [
            {"n": i + 1, "text": r.get("text", "")}
            for i, r in enumerate(reviews)
        ]
        reviews_json = json.dumps(numbered, ensure_ascii=False, indent=2)
        return self.BATCH_CLASSIFICATION_PROMPT.format(
            reviews_json=reviews_json,
            batch_size=len(reviews),
        )

    def _parse_batch_response(
        self, raw: str, batch_size: int
    ) -> list[ClassificationResult]:
        """Parse the model's JSON array response into ClassificationResult list.

        Steps:
        1. Strip <think>...</think> blocks
        2. Extract JSON array: bracket-slice first (`[`...`]`), fence regex fallback
        3. Parse JSON array
        4. Validate each item: required fields, product_area in VALID_PRODUCT_AREAS
        5. On any failure: return list of parse_failed=True results

        Never raises. Always returns list of length batch_size.
        """
        failed = [self._make_parse_failed_result(raw) for _ in range(batch_size)]

        try:
            # Step 1: strip <think>...</think> blocks
            cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

            # Step 2: extract JSON array — bracket-slice first, fence regex fallback.
            # Bracket-slicing handles preamble text like "Here is the JSON array:"
            # that anchored fence regexes fail on.
            bracket_start = cleaned.find("[")
            bracket_end = cleaned.rfind("]")
            if bracket_start != -1 and bracket_end != -1 and bracket_end > bracket_start:
                cleaned = cleaned[bracket_start:bracket_end + 1]
            else:
                fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL)
                if fence_match:
                    cleaned = fence_match.group(1).strip()

            # Step 3: parse JSON
            parsed = json.loads(cleaned)
            if not isinstance(parsed, list):
                self._logger.warning(
                    "Expected JSON array, got %s", type(parsed).__name__
                )
                return failed

            # Step 4: validate each item
            results: list[ClassificationResult] = []
            required_keys = frozenset(
                ["product_area", "specific_feature_request", "workflow_breakdown", "confidence"]
            )
            for item in parsed:
                if not isinstance(item, dict):
                    self._logger.warning("Item is not a dict: %r", item)
                    return failed

                if not required_keys.issubset(item.keys()):
                    self._logger.warning("Missing fields in item: %s", item)
                    return failed

                product_area = item["product_area"]
                if product_area not in self.VALID_PRODUCT_AREAS:
                    self._logger.warning("Invalid product_area: %r", product_area)
                    return failed

                # Bug 5: bool("false") == True. Strict parsing required.
                try:
                    workflow_breakdown = self._parse_bool(item["workflow_breakdown"])
                except ValueError:
                    self._logger.warning(
                        "Invalid workflow_breakdown value %r — rejecting row",
                        item.get("workflow_breakdown"),
                    )
                    results.append(self._make_parse_failed_result(raw))
                    continue

                results.append(ClassificationResult(
                    product_area=str(product_area),
                    specific_feature_request=item["specific_feature_request"],
                    workflow_breakdown=workflow_breakdown,
                    confidence=float(item["confidence"]),
                    raw_response=raw,
                    parse_failed=False,
                ))

            if len(results) < batch_size:
                self._logger.warning(
                    "Response has %d items, expected %d. Padding with failures.",
                    len(results), batch_size,
                )
                results.extend(
                    self._make_parse_failed_result(raw)
                    for _ in range(batch_size - len(results))
                )

            return results[:batch_size]

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self._logger.warning("Failed to parse batch response: %s", exc)
            return failed

    @staticmethod
    def _parse_bool(value: object) -> bool:
        """Parse a boolean from an API response value.

        API models sometimes return "true"/"false" strings instead of JSON
        booleans. bool("false") == True is a silent corruption bug — this
        method raises ValueError for any unrecognised value so the row is
        rejected rather than silently corrupted.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            if value.lower() == "true":
                return True
            if value.lower() == "false":
                return False
        raise ValueError(f"Cannot parse bool from {value!r}")

    def _make_parse_failed_result(self, raw: str = "") -> ClassificationResult:
        """Return a ClassificationResult indicating parse failure.

        Uses "unclassified" as the product_area sentinel (not "ux") so that
        failed classifications don't pollute the UX bucket in SQL aggregates.
        """
        return ClassificationResult(
            product_area="unclassified",
            specific_feature_request=None,
            workflow_breakdown=False,
            confidence=0.0,
            raw_response=raw,
            parse_failed=True,
        )
