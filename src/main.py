#!/usr/bin/env python3
"""Pipeline entry point for fintech-review-intelligence.

Runs all phases in order. Checks pipeline_state checkpoints before
each phase — skips phases already marked complete.

Usage:
    python src/main.py                    # run all phases
    python src/main.py --phase collection # run one phase only
    python src/main.py --dry-run          # mock API calls, test pipeline wiring
"""
import argparse
import json
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from src.config import Config
from src.data_collection.database_manager import DatabaseManager
from src.analysis.sql_analyst import SQLAnalyst
from src.analysis.findings_summarizer import FindingsSummarizer
from src.classification.review_classifier import ReviewClassifier
from src.classification.batch_processor import BatchProcessor
from src.council.council_orchestrator import CouncilOrchestrator


def setup_logging() -> None:
    """Configure root logger for the pipeline."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def ensure_outputs_dir() -> None:
    """Create outputs/ directory if it does not exist."""
    os.makedirs("outputs", exist_ok=True)


def main() -> None:
    """Run the fintech review intelligence pipeline end to end.

    Phases run in order. Each phase checks pipeline_state first —
    already-complete phases are skipped automatically.
    Failed phases raise and stop the pipeline immediately.
    """
    setup_logging()
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

    load_dotenv()
    config = Config.from_env()
    Config.setup_logging()

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
            state = db.get_phase_state("collection")
            if state and state["status"] == "complete":
                logger.info("Phase 1 (collection) already complete — skipping")
            elif args.dry_run:
                logger.info("DRY RUN — skipping Phase 1 (collection)")
                db.save_phase_state("collection", "complete", {"dry_run": True, "total_collected": 0})
            else:
                logger.info("Phase 1: Data Collection")
                # Lazy import: google_play_scraper only needed when actually collecting
                from src.data_collection.review_collector import ReviewCollector  # noqa: PLC0415
                collector = ReviewCollector(db=db, config=config)
                result = collector.collect_all(target_per_app=2500)
                logger.info(
                    "Collection complete — %d reviews across %d apps",
                    result.total_collected,
                    len(result.per_app),
                )

        # ----------------------------------------------------------------
        # Phase 2: SQL Analysis
        # ----------------------------------------------------------------
        if args.phase in (None, "analysis"):
            state = db.get_phase_state("analysis")
            if state and state["status"] == "complete":
                logger.info("Phase 2 (analysis) already complete — skipping")
            elif args.dry_run:
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
                db.save_phase_state("analysis", "complete", {"dry_run": True})
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
                logger.info("Phase 3: Semantic Classification")
                if args.dry_run:
                    logger.info("DRY RUN — skipping classification API calls")
                    db.save_phase_state(
                        "classification", "complete",
                        {"dry_run": True, "total_classified": 0},
                    )
                else:
                    classifier = ReviewClassifier(config=config)
                    processor = BatchProcessor(classifier=classifier, db=db)
                    batch_result = processor.run()
                    logger.info(
                        "Classification complete — %d reviews, %d failures",
                        batch_result.total_classified,
                        batch_result.parse_failures,
                    )

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
                with open(summary_path, encoding="utf-8") as f:
                    summary_data = json.load(f)
                findings_text = summary_data.get("structured_text", "")

                if args.dry_run:
                    logger.info("DRY RUN — using mock council output")
                    mock_synthesis = (
                        "DRY RUN MOCK: Council synthesis placeholder. "
                        "This is a dry-run output with no real LLM calls. "
                        "Run without --dry-run to generate real insights from "
                        "the 4-model council. The pipeline wiring is verified "
                        "and all phases completed successfully."
                    )
                    db.save_phase_state("council", "complete", {"dry_run": True})
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
                    orchestrator = CouncilOrchestrator.default(config=config)
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

            with open(council_path, encoding="utf-8") as f:
                council_data = json.load(f)
            with open(summary_path, encoding="utf-8") as f:
                summary_data = json.load(f)

            from src.agents.insight_reporter import InsightReporter
            reporter = InsightReporter.from_dicts(council_data, summary_data)
            report_result = reporter.generate_all()

            logger.info("Report complete:")
            logger.info("  findings_report.md → %s", report_result.report_path)
            logger.info("  linkedin_snippet.txt → %s", report_result.linkedin_path)
            logger.info("  README.md → %s", report_result.readme_path)
            logger.info("  Word count: %d", report_result.word_count)

    logger.info("Pipeline complete")


if __name__ == "__main__":
    main()
