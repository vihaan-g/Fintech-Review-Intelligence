"""Generates the final markdown report and LinkedIn snippet from council output."""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class InsightReporter:
    """Produces findings_report.md, linkedin_snippet.txt, and README.md."""

    def __init__(self, output_dir: str = "outputs") -> None:
        """Initialize with the output directory path.

        Args:
            output_dir: Directory where report files will be written.
        """
        self.output_dir = output_dir

    def generate_report(self, council_result: Dict) -> None:
        """Write findings_report.md from council output, leading with findings."""
        raise NotImplementedError
