#!/usr/bin/env python3
"""Pipeline entry point for fintech-review-intelligence.

Runs all phases in order. Checks pipeline_state checkpoints before
each phase — skips phases already marked complete.

Usage:
    python src/main.py                    # run all phases
    python src/main.py --phase collection # run one phase only
    python src/main.py --dry-run          # mock API calls, test pipeline wiring
"""
import os
import sys

# When run as `python src/main.py`, Python adds src/ to sys.path.
# Insert the project root so `from src.X import Y` absolute imports resolve.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import argparse
import json
import logging
import signal
from datetime import datetime, timezone

from dotenv import load_dotenv

from src.config import Config
from src.data_collection.database_manager import DatabaseManager
from src.analysis.sql_analyst import SQLAnalyst
from src.analysis.findings_summarizer import FindingsSummarizer
from src.classification.review_classifier import ReviewClassifier
from src.classification.batch_processor import BatchProcessor
from src.council.council_orchestrator import CouncilOrchestrator


def ensure_outputs_dir() -> None:
    """Create outputs/ directory if it does not exist."""
    os.makedirs("outputs", exist_ok=True)


def _install_sigint_handler() -> None:
    """Install a clean SIGINT handler that logs and exits 130 instead of traceback."""
    def _handler(sig: int, frame: object) -> None:
        logging.getLogger(__name__).info(
            "Interrupted (SIGINT) — progress checkpointed. Re-run to resume."
        )
        sys.exit(130)
    signal.signal(signal.SIGINT, _handler)


def main() -> None:
    """Run the fintech review intelligence pipeline end to end.

    Phases run in order. Each phase checks pipeline_state first —
    already-complete phases are skipped automatically.
    Failed phases raise and stop the pipeline immediately.
    """
    ensure_outputs_dir()
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(
        description="Fintech Review Intelligence Pipeline"
    )
    parser.add_argument(
        "--phase",
        choices=["collection", "analysis", "classification", "council", "report"],
        help="Run a single phase only (skips all others)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip all external API calls. Uses mock data for council and classification.",
    )
    args = parser.parse_args()

    Config.setup_logging()
    _install_sigint_handler()

    load_dotenv()
    config = Config.from_env()

    logger.info(
        "Pipeline starting — dry_run=%s phase=%s",
        args.dry_run,
        args.phase or "all",
    )

    with DatabaseManager() as db:
        db.create_schema()

        # ----------------------------------------------------------------
        # Phase 1: Data Collection
        # ----------------------------------------------------------------
        if args.phase in (None, "collection"):
            # Bug 9: use per-app checkpoint keys (collection_<app>), not a single
            # "collection" key. The collector writes per-app keys; reading them here
            # keeps both paths consistent and makes partial-collection resumable.
            _COLLECTION_APP_KEYS = ["groww", "jupiter", "cred", "phonepe", "paytm"]
            per_app_states = {
                k: db.get_phase_state(f"collection_{k}") for k in _COLLECTION_APP_KEYS
            }
            all_apps_complete = all(
                s is not None and s.get("status") == "complete"
                for s in per_app_states.values()
            )
            if all_apps_complete:
                logger.info("Phase 1 (collection) already complete for all apps — skipping")
            elif args.dry_run:
                # Bug 1: dry-run must not write canonical phase state
                logger.info("DRY RUN — skipping Phase 1 (collection)")
            else:
                logger.info("Phase 1: Data Collection")
                # Lazy import: google_play_scraper only needed when actually collecting
                from src.data_collection.review_collector import ReviewCollector  # noqa: PLC0415
                collector = ReviewCollector(db=db, config=config)
                result = collector.collect_all(target_per_app=2200)
                logger.info(
                    "Collection complete — %d reviews across %d apps",
                    result.total_collected,
                    len(result.per_app),
                )
                # Bug 2: halt before analysis if any apps failed
                if result.failed_apps:
                    raise RuntimeError(
                        f"Collection failed for {len(result.failed_apps)} app(s): "
                        f"{result.failed_apps}. Fix errors above and re-run to retry."
                    )

        # ----------------------------------------------------------------
        # Phase 2: SQL Analysis
        # ----------------------------------------------------------------
        if args.phase in (None, "analysis"):
            state = db.get_phase_state("analysis")
            if state and state["status"] == "complete":
                logger.info("Phase 2 (analysis) already complete — skipping")
            elif args.dry_run:
                # Bug 1: dry-run must not write canonical phase state
                logger.info("DRY RUN — writing mock findings_summary.json")
                mock_summary = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "cross_app_stats": {},
                    "high_signal_reviews": [],
                    "keyword_frequencies": {},
                    "rating_trends": [],
                    "developer_reply_impact": {},
                    "volume_spikes": [],
                    "structured_text": "## Data Overview\nDRY RUN — no real data collected.",
                }
                os.makedirs("outputs", exist_ok=True)
                with open("outputs/findings_summary.json", "w", encoding="utf-8") as f:
                    json.dump(mock_summary, f, indent=2)
            else:
                logger.info("Phase 2: SQL Analysis")
                analyst = SQLAnalyst(db=db)
                summarizer = FindingsSummarizer(analyst=analyst)
                summary = summarizer.generate_summary()
                summarizer.save_to_file(summary)
                db.save_phase_state("analysis", "complete")
                logger.info("Analysis complete — summary saved to outputs/")

        # ----------------------------------------------------------------
        # Phase 3: Classification
        # ----------------------------------------------------------------
        if args.phase in (None, "classification"):
            state = db.get_phase_state("classification")
            if state and state["status"] == "complete":
                logger.info("Phase 3 (classification) already complete — skipping")
            else:
                if state and state.get("status") == "in_progress":
                    logger.info("Phase 3: Semantic Classification — resuming from checkpoint")
                else:
                    logger.info("Phase 3: Semantic Classification")
                if args.dry_run:
                    # Bug 1: dry-run must not write canonical phase state
                    logger.info("DRY RUN — skipping classification API calls")
                    analyst = SQLAnalyst(db=db)
                    summarizer = FindingsSummarizer(analyst=analyst)
                    summarizer.enrich_with_classification()  # logs warning, returns False
                else:
                    classifier = ReviewClassifier(config=config)
                    processor = BatchProcessor(classifier=classifier, db=db)
                    batch_result = processor.run()

                    if batch_result.status == "complete":
                        db.save_phase_state("classification", "complete", {
                            "total_classified": batch_result.total_classified,
                            "parse_failures": batch_result.parse_failures,
                        })
                        logger.info(
                            "Classification complete — %d reviews, %d failures",
                            batch_result.total_classified,
                            batch_result.parse_failures,
                        )
                        analyst = SQLAnalyst(db=db)
                        summarizer = FindingsSummarizer(analyst=analyst)
                        enriched = summarizer.enrich_with_classification()
                        if enriched:
                            logger.info("findings_summary.json enriched with classification data")
                        else:
                            logger.warning(
                                "No classified reviews found — findings_summary.json not enriched"
                            )
                    elif batch_result.status == "incomplete":
                        db.save_phase_state("classification", "in_progress", {
                            "total_classified": batch_result.total_classified,
                            "remaining_unclassified": batch_result.remaining_unclassified,
                        })
                        logger.error(
                            "Classification incomplete: %d reviews still unclassified "
                            "after iteration cap (%d classified). Re-run to resume.",
                            batch_result.remaining_unclassified,
                            batch_result.total_classified,
                        )
                        sys.exit(1)
                    elif batch_result.status == "quota_exhausted":
                        db.save_phase_state("classification", "in_progress", {
                            "total_classified": batch_result.total_classified,
                        })
                        logger.error(
                            "Gemini daily quota exhausted after %d reviews classified. "
                            "Re-run tomorrow to resume.",
                            batch_result.total_classified,
                        )
                        sys.exit(1)
                    elif batch_result.status == "auth_error":
                        db.save_phase_state("classification", "in_progress")
                        logger.critical(
                            "Gemini authentication failed — check GEMINI_API_KEY in .env. "
                            "Classification has NOT been marked complete."
                        )
                        sys.exit(1)
                    elif batch_result.status == "network_error":
                        db.save_phase_state("classification", "in_progress", {
                            "total_classified": batch_result.total_classified,
                        })
                        logger.error(
                            "Network error after all retries — %d reviews classified before "
                            "failure. Re-run to resume. Error: %s",
                            batch_result.total_classified,
                            batch_result.message,
                        )
                        sys.exit(1)

        # ----------------------------------------------------------------
        # Phase 4: Council
        # ----------------------------------------------------------------
        if args.phase in (None, "council"):
            state = db.get_phase_state("council")
            if state and state["status"] == "complete":
                logger.info("Phase 4 (council) already complete — skipping")
            else:
                logger.info("Phase 4: LLM Council")
                summary_path = "outputs/findings_summary.json"
                if not os.path.exists(summary_path):
                    raise FileNotFoundError(
                        f"findings_summary.json not found at {summary_path}. "
                        "Run Phase 2 (analysis) first."
                    )
                try:
                    with open(summary_path, encoding="utf-8") as f:
                        summary_data = json.load(f)
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError(
                        f"Could not read {summary_path}: {exc}. "
                        "The file may be corrupted — re-run Phase 2 (analysis)."
                    ) from exc
                findings_text = summary_data.get("structured_text", "")

                if args.dry_run:
                    # Bug 1: dry-run must not write canonical phase state
                    logger.info("DRY RUN — using mock council output")
                    mock_synthesis = (
                        "DRY RUN MOCK: Council synthesis placeholder. "
                        "This is a dry-run output with no real LLM calls. "
                        "Run without --dry-run to generate real insights from "
                        "the 4-model council. The pipeline wiring is verified "
                        "and all phases completed successfully."
                    )
                    mock_result = {
                        "stage3_synthesis": mock_synthesis,
                        "stage2_gap_analysis": "mock gap analysis",
                        "stage1_responses": {},
                        "anonymization_map": {},
                        "total_duration_ms": 0,
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    os.makedirs("outputs", exist_ok=True)
                    with open("outputs/council_result.json", "w", encoding="utf-8") as f:
                        json.dump(mock_result, f, indent=2)
                else:
                    orchestrator = CouncilOrchestrator.default(config=config, db=db)
                    council_result = orchestrator.run_sync(findings_text)
                    db.save_phase_state("council", "complete")
                    logger.info("Council complete — synthesis ready")

        # ----------------------------------------------------------------
        # Phase 5: Report
        # ----------------------------------------------------------------
        if args.phase in (None, "report"):
            logger.info("Phase 5: Report Generation")
            council_path = "outputs/council_result.json"
            summary_path = "outputs/findings_summary.json"

            if not os.path.exists(council_path):
                raise FileNotFoundError(
                    "council_result.json not found. Run Phase 4 (council) first."
                )
            if not os.path.exists(summary_path):
                raise FileNotFoundError(
                    "findings_summary.json not found. Run Phase 2 (analysis) first."
                )

            try:
                with open(council_path, encoding="utf-8") as f:
                    council_data = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Could not read {council_path}: {exc}. "
                    "The file may be corrupted — re-run Phase 4 (council)."
                ) from exc
            try:
                with open(summary_path, encoding="utf-8") as f:
                    summary_data = json.load(f)
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError(
                    f"Could not read {summary_path}: {exc}. "
                    "The file may be corrupted — re-run Phase 2 (analysis)."
                ) from exc

            from src.agents.insight_reporter import InsightReporter
            reporter = InsightReporter.from_dicts(council_data, summary_data)
            report_result = reporter.generate_all()

            logger.info("Report complete:")
            logger.info("  findings_report.md → %s", report_result.report_path)
            logger.info("  linkedin_snippet.txt → %s", report_result.linkedin_path)
            logger.info("  README.md → %s", report_result.readme_path)
            logger.info("  Word count: %d", report_result.word_count)

    logger.info("Pipeline complete")


def _format_recovery_hint(exc: BaseException) -> str:
    """Map common exceptions to one-line recovery hints for the operator.

    The pipeline runs unattended for ~2 hours; a raw traceback at the end is
    not enough context for someone who didn't write this code to recover.
    """
    # Avoid import-at-top cycles — these classes are only needed for the hint.
    from src.classification.review_classifier import (  # noqa: PLC0415
        GeminiAuthError, GeminiQuotaExhaustedError,
    )
    if isinstance(exc, GeminiAuthError):
        return (
            "Fix: verify GEMINI_API_KEY in .env is correct and active at "
            "https://aistudio.google.com/app/apikey — then re-run; completed "
            "phases will be skipped."
        )
    if isinstance(exc, GeminiQuotaExhaustedError):
        return (
            "Fix: Gemini Tier 1 paid daily quota is exhausted. "
            "Wait until UTC midnight, then re-run — classification will "
            "resume from checkpoint."
        )
    if isinstance(exc, FileNotFoundError):
        return (
            "Fix: re-run with no --phase flag. Earlier phases will regenerate "
            "the missing file."
        )
    if isinstance(exc, ValueError) and "Missing required environment" in str(exc):
        return (
            "Fix: copy .env.example to .env and fill in GEMINI_API_KEY and "
            "OPENROUTER_API_KEY, then re-run."
        )
    if isinstance(exc, RuntimeError) and "All Stage 1 members returned empty" in str(exc):
        return (
            "Fix: check outputs/council_stage1_raw.json for the raw responses. "
            "Most likely causes: (1) OpenRouter :free daily quota hit — wait "
            "and re-run, (2) bad OPENROUTER_API_KEY, (3) model IDs withdrawn "
            "from OpenRouter :free (update council_orchestrator.default())."
        )
    return (
        "Fix: inspect the traceback above, check outputs/ for partial state, "
        "and re-run — completed phases are checkpointed and will be skipped."
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass  # handled by SIGINT handler installed in main()
    except Exception as exc:  # noqa: BLE001
        _logger = logging.getLogger(__name__)
        _logger.error("Pipeline failed: %s: %s", type(exc).__name__, exc, exc_info=True)
        _logger.error("Recovery: %s", _format_recovery_hint(exc))
        sys.exit(1)
