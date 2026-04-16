"""Classifies Play Store reviews using Gemini 2.5 Flash."""
import json
import logging
import re
import time
from dataclasses import dataclass

import httpx

from src.config import Config

_GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.5-flash:generateContent"
)
_MAX_RETRIES = 3
_RETRYABLE_STATUS_CODES = frozenset([429, 500, 502, 503])
_REQUEST_TIMEOUT_SECONDS = 30.0


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
    """Classifies Play Store reviews using Gemini 2.5 Flash.

    Uses prompt-optimizer output for classification prompts.
    Never raises on parse failure — returns low-confidence result instead.
    All API calls go through _call_gemini() for testability.
    """

    SINGLE_REVIEW_PROMPT: str = (
        "You are a classification engine for Play Store reviews of Indian fintech apps "
        "(Fi Money, Jupiter, CRED, PhonePe).\n\n"
        "TASK: Classify the review below into exactly one product area and extract a "
        "specific feature request if and only if one is explicitly stated in the review text.\n\n"
        "PRODUCT AREAS (choose exactly one):\n"
        "- onboarding   \u2014 account setup, KYC, sign-up, first-use friction\n"
        "- ux           \u2014 navigation, UI design, confusing flows, app usability\n"
        "- transactions \u2014 payments, UPI, transfers, bill pay, recharges\n"
        "- support      \u2014 customer care, chat support, complaint resolution\n"
        "- performance  \u2014 crashes, slowness, bugs, error messages, login failures\n"
        "- trust        \u2014 security, fraud, scam, data privacy, verification failures\n\n"
        "RULES:\n"
        "1. product_area MUST be one of the six values listed \u2014 no other values are valid\n"
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
        "(Fi Money, Jupiter, CRED, PhonePe).\n\n"
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
        "support", "performance", "trust",
    ])

    def __init__(self, config: Config) -> None:
        """Initialise classifier. Extracts Gemini API key from config."""
        self._api_key: str = config.gemini_api_key
        self._logger = logging.getLogger(self.__class__.__name__)

    def classify_batch(self, reviews: list[dict]) -> list[ClassificationResult]:
        """Classify a batch of reviews in a single Gemini API call.

        Sends all reviews as a JSON array in one prompt.
        Expects a JSON array response of the same length.
        On any parse failure: returns list of parse_failed=True results
        for the entire batch. Never raises. Always returns same length as input.

        Args:
            reviews: list of review dicts with at minimum a 'text' field

        Returns:
            list of ClassificationResult, one per input review, same order
        """
        if not reviews:
            return []

        prompt = self._build_batch_prompt(reviews)
        try:
            raw = self._call_gemini(prompt)
        except Exception as exc:
            self._logger.warning("Gemini API call failed for batch of %d: %s", len(reviews), exc)
            return [self._make_parse_failed_result() for _ in reviews]

        return self._parse_batch_response(raw, batch_size=len(reviews))

    def _call_gemini(self, prompt: str) -> str:
        """Make a single synchronous HTTP POST to Gemini 2.5 Flash.

        Endpoint:
            https://generativelanguage.googleapis.com/v1beta/models/
            gemini-2.5-flash:generateContent?key={api_key}

        Request body:
            {"contents": [{"parts": [{"text": prompt}]}]}

        Returns the model's text response as a raw string.
        Raises httpx.HTTPStatusError on non-200 responses after retries exhausted.
        Timeout: 30 seconds.
        """
        url = f"{_GEMINI_ENDPOINT}?key={self._api_key}"
        body = {"contents": [{"parts": [{"text": prompt}]}]}
        last_exc: Exception = RuntimeError("All retries exhausted")

        for attempt in range(_MAX_RETRIES):
            try:
                response = httpx.post(url, json=body, timeout=_REQUEST_TIMEOUT_SECONDS)
                response.raise_for_status()
                data = response.json()
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS_CODES:
                    raise
                last_exc = exc
                self._logger.warning(
                    "Retryable HTTP %d on attempt %d/%d",
                    exc.response.status_code, attempt + 1, _MAX_RETRIES,
                )
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.TimeoutException) as exc:
                last_exc = exc
                self._logger.warning(
                    "Network error on attempt %d/%d: %s", attempt + 1, _MAX_RETRIES, exc,
                )

            if attempt < _MAX_RETRIES - 1:
                time.sleep(2 ** attempt)

        raise last_exc

    def _build_batch_prompt(self, reviews: list[dict]) -> str:
        """Build the batch classification prompt for a list of reviews.

        Inserts reviews as a numbered JSON array into BATCH_CLASSIFICATION_PROMPT.
        Returns the complete prompt string ready to send to Gemini.
        """
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
        """Parse Gemini's JSON array response into ClassificationResult list.

        Steps:
        1. Strip <think>...</think> blocks (Qwen3 safety — note from CLAUDE.md)
        2. Strip markdown fences if present (```json ... ```)
        3. Parse JSON array
        4. Validate each item: required fields present, product_area in VALID_PRODUCT_AREAS
        5. On any failure at any step: return list of parse_failed=True results

        Never raises. Always returns list of length batch_size.
        """
        failed = [self._make_parse_failed_result(raw) for _ in range(batch_size)]

        try:
            # Step 1: strip <think>...</think> blocks
            cleaned = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()

            # Step 2: strip markdown fences
            fence_match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
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

                results.append(ClassificationResult(
                    product_area=str(product_area),
                    specific_feature_request=item["specific_feature_request"],
                    workflow_breakdown=bool(item["workflow_breakdown"]),
                    confidence=float(item["confidence"]),
                    raw_response=raw,
                    parse_failed=False,
                ))

            # Pad with failures if response is shorter than expected
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

    def _make_parse_failed_result(self, raw: str = "") -> ClassificationResult:
        """Return a ClassificationResult indicating parse failure."""
        return ClassificationResult(
            product_area="ux",
            specific_feature_request=None,
            workflow_breakdown=False,
            confidence=0.0,
            raw_response=raw,
            parse_failed=True,
        )
