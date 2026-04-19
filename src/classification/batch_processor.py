"""Orchestrates classification of all unclassified reviews in the database."""
import json
import logging
import math
import os
import time
from dataclasses import asdict, dataclass

from src.classification.review_classifier import (
    GeminiAuthError,
    GeminiNetworkError,
    GeminiQuotaExhaustedError,
    ReviewClassifier,
)
from src.data_collection.database_manager import DatabaseManager


@dataclass
class BatchResult:
    """Summary of a completed classification run."""

    total_classified: int
    parse_failures: int
    status: str  # "complete" | "quota_exhausted" | "auth_error" | "network_error"
    batches_processed: int = 0
    duration_seconds: float = 0.0
    message: str = ""  # human-readable explanation for non-complete status


class BatchProcessor:
    """Orchestrates classification of all unclassified reviews in the database.

    Applies cost-aware-llm-pipeline patterns:
    - Batches reviews to minimise API calls
    - Respects Gemini free tier rate limit (14 RPM = 4.3s sleep between calls)
    - Checkpoints after each batch so interrupted runs resume correctly
    - Estimates token cost before starting full run
    """

    BATCH_SIZE: int = 10
    SLEEP_BETWEEN_BATCHES: float = 4.3  # keeps under 14 RPM (60/14 ≈ 4.29s)

    def __init__(self, classifier: ReviewClassifier, db: DatabaseManager) -> None:
        """Initialise processor with injected classifier and database."""
        self._classifier = classifier
        self._db = db
        self._logger = logging.getLogger(self.__class__.__name__)

    def run(self) -> BatchResult:
        """Classify all unclassified reviews. Resume from checkpoint if interrupted.

        Workflow:
        1. Check pipeline_state 'classification' — if 'complete', log and return
           BatchResult with zeros (do not re-classify)
        2. Estimate total API calls needed: ceil(unclassified_count / BATCH_SIZE)
           Log: "Classification estimate: {n} batches, ~{minutes}min at 14 RPM"
        3. Mark pipeline_state 'classification' as 'in_progress'
        4. Loop: fetch BATCH_SIZE unclassified reviews, classify, save to DB
        5. Log every 10 batches:
           "Classified {n}/{total} reviews ({pct:.1f}%) — {failures} parse failures"
        6. Sleep SLEEP_BETWEEN_BATCHES between each batch
        7. On completion: mark pipeline_state 'complete' with BatchResult metadata
        8. Save BatchResult to outputs/classification_complete.json
        9. Return BatchResult
        """
        # Step 1: check if already complete
        state = self._db.get_phase_state("classification")
        if state is not None and state.get("status") == "complete":
            self._logger.info("Classification phase already complete. Skipping.")
            return BatchResult(
                total_classified=0,
                parse_failures=0,
                batches_processed=0,
                duration_seconds=0.0,
                status="complete",
            )

        # Step 2: estimate — use COUNT queries rather than loading all rows,
        # so a resumed run reports accurate remaining work without pulling
        # ~10k reviews into memory just to len() them.
        unclassified_total = self._db.get_unclassified_count()
        already_classified = self._db.get_classified_count()

        if unclassified_total == 0:
            self._logger.info("No unclassified reviews found. Nothing to do.")
            self._db.save_phase_state("classification", "complete")
            return BatchResult(
                total_classified=0,
                parse_failures=0,
                batches_processed=0,
                duration_seconds=0.0,
                status="complete",
            )

        n_batches = math.ceil(unclassified_total / self.BATCH_SIZE)
        est_minutes = (n_batches * self.SLEEP_BETWEEN_BATCHES) / 60.0
        if already_classified > 0:
            self._logger.info(
                "Resuming classification: %d already classified, %d remaining "
                "— estimate %d batches, ~%.1fmin at 14 RPM",
                already_classified, unclassified_total, n_batches, est_minutes,
            )
        else:
            self._logger.info(
                "Classification estimate: %d batches, ~%.1fmin at 14 RPM",
                n_batches, est_minutes,
            )

        # Step 3: mark in_progress
        self._db.save_phase_state("classification", "in_progress")

        total_classified = 0
        parse_failures = 0
        batches_processed = 0
        start_time = time.monotonic()

        # Step 4: loop — re-fetch each time so checkpointing works correctly.
        # H2: cap iterations to prevent infinite loop if H1 (empty review_id) fires.
        max_iterations = math.ceil(unclassified_total / self.BATCH_SIZE) + 5
        quota_exhausted = False
        auth_error = False
        auth_message = ""
        network_error = False
        network_message = ""
        iteration = 0
        while iteration < max_iterations:
            batch = self._db.get_unclassified_reviews(limit=self.BATCH_SIZE)
            if not batch:
                break

            try:
                results = self._classifier.classify_batch(batch)
            except GeminiQuotaExhaustedError as exc:
                self._logger.error(
                    "Gemini daily quota exhausted after %d batches. "
                    "Progress checkpointed — re-run tomorrow to resume. (%s)",
                    batches_processed, exc,
                )
                quota_exhausted = True
                break
            except GeminiAuthError as exc:
                self._logger.error(
                    "Gemini authentication failed after %d batches — "
                    "check GEMINI_API_KEY. (%s)",
                    batches_processed, exc,
                )
                auth_error = True
                auth_message = str(exc)
                break
            except GeminiNetworkError as exc:
                self._logger.error(
                    "Network error after all retries, %d batches processed. (%s)",
                    batches_processed, exc,
                )
                network_error = True
                network_message = str(exc)
                break

            for review, result in zip(batch, results):
                classification_json = json.dumps({
                    "product_area": result.product_area,
                    "specific_feature_request": result.specific_feature_request,
                    "workflow_breakdown": result.workflow_breakdown,
                    "confidence": result.confidence,
                    "parse_failed": result.parse_failed,
                })
                self._db.update_classification(review["review_id"], classification_json)
                total_classified += 1
                if result.parse_failed:
                    parse_failures += 1

            batches_processed += 1
            iteration += 1

            # Step 5: DEBUG log every batch, INFO every 10 batches
            pct = (total_classified / unclassified_total) * 100
            self._logger.debug(
                "Batch %d: classified %d/%d (%.1f%%) — %d parse failures",
                batches_processed, total_classified, unclassified_total, pct, parse_failures,
            )
            if batches_processed % 10 == 0:
                self._logger.info(
                    "Classified %d/%d reviews (%.1f%%) \u2014 %d parse failures",
                    total_classified, unclassified_total, pct, parse_failures,
                )

            # Step 6: sleep to respect 14 RPM free tier limit
            time.sleep(self.SLEEP_BETWEEN_BATCHES)

        duration = time.monotonic() - start_time

        if auth_error:
            status = "auth_error"
            message = auth_message
        elif quota_exhausted:
            status = "quota_exhausted"
            message = ""
        elif network_error:
            status = "network_error"
            message = network_message
        else:
            status = "complete"
            message = ""

        result_summary = BatchResult(
            total_classified=total_classified,
            parse_failures=parse_failures,
            batches_processed=batches_processed,
            duration_seconds=duration,
            status=status,
            message=message,
        )

        # Step 7: checkpoint to DB — 'complete' only when all reviews processed.
        final_status = "complete" if status == "complete" else "in_progress"
        self._db.save_phase_state(
            "classification",
            final_status,
            {
                "total_classified": total_classified,
                "parse_failures": parse_failures,
                "batches_processed": batches_processed,
                "status": status,
            },
        )

        # Step 8: save to file (always — useful for debugging partial runs)
        self._save_result(result_summary)

        return result_summary

    def _save_result(self, result: BatchResult) -> None:
        """Serialize BatchResult to outputs/classification_complete.json.

        Canonical state lives in pipeline_state (DB). This file is debug
        metadata — a failure here is logged loudly with the counts embedded so
        the user can reconstruct progress without the file, but does not abort
        the pipeline (phase status is already written to the DB).
        """
        try:
            os.makedirs("outputs", exist_ok=True)
        except OSError as exc:
            self._logger.error(
                "Could not create outputs/ directory for BatchResult debug "
                "file: %s. DB phase state is authoritative — classified=%d, "
                "failures=%d, batches=%d.",
                exc, result.total_classified, result.parse_failures, result.batches_processed,
            )
            return

        output_path = "outputs/classification_complete.json"
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(asdict(result), f, indent=2)
            self._logger.info("BatchResult saved to %s", output_path)
        except OSError as exc:
            self._logger.error(
                "Failed to save BatchResult debug file to %s: %s. "
                "DB phase state is authoritative — classified=%d, failures=%d, "
                "batches=%d. Pipeline will continue.",
                output_path, exc,
                result.total_classified, result.parse_failures, result.batches_processed,
            )
