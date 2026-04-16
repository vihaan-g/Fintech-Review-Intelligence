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
import logging
import os
from dotenv import load_dotenv

from src.config import Config
from src.council.council_orchestrator import CouncilOrchestrator  # noqa: F401
from src.analysis.findings_summarizer import FindingsSummarizer  # noqa: F401


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
    """Parse arguments and run the pipeline."""
    setup_logging()
    ensure_outputs_dir()

    parser = argparse.ArgumentParser(description="Fintech Review Intelligence Pipeline")
    parser.add_argument(
        "--phase",
        choices=["collection", "analysis", "classification", "council", "report"],
        help="Run a single phase only",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without real API calls (uses mock data)",
    )
    args = parser.parse_args()

    load_dotenv()
    config = Config.from_env()

    logger = logging.getLogger(__name__)
    logger.info(
        "Pipeline starting — dry_run=%s, phase=%s",
        args.dry_run,
        args.phase or "all",
    )

    # Phase 4: Council — wired in Stage 6 (full integration)
    # CouncilOrchestrator.default(config).run_sync(findings_summary.structured_text)

    # Phase implementations will be wired here in Stage 6
    logger.info("Pipeline complete")


if __name__ == "__main__":
    main()
