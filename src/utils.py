"""Pure utility functions and lightweight helpers shared across the pipeline."""
import re
from typing import Any


def extract_top_findings(synthesis: str, n: int = 3) -> list[str]:
    """Extract top N finding lines from a council synthesis string.

    Primary: lines starting with ``**Finding`` or ``**Insight``.
    Secondary: ``### Finding`` / ``### Insight`` / numeric-prefixed lines.
    Fallback: first N non-empty sentences (>20 chars).
    Final fallback: a single generic pointer line (never empty).
    """
    findings: list[str] = []

    def _clean(line: str) -> str:
        stripped = line.strip().lstrip("#").strip().lstrip("*").strip()
        stripped = re.sub(r"^\d+[.)]\s*", "", stripped)
        if ":" in stripped:
            head, tail = stripped.split(":", 1)
            if len(head) <= 40:
                stripped = tail.strip()
        return stripped.strip("*").strip()

    for line in synthesis.splitlines():
        stripped = line.strip()
        if (
            stripped.startswith("**Finding")
            or stripped.startswith("**Insight")
            or stripped.startswith("### Finding")
            or stripped.startswith("### Insight")
            or re.match(r"^\d+[.)]\s", stripped)
        ):
            cleaned = _clean(stripped)
            if not cleaned or len(cleaned) < 20:
                cleaned = stripped.lstrip("#").lstrip("*").strip()
            if cleaned:
                findings.append(cleaned)
        if len(findings) >= n:
            break

    if not findings:
        flat = synthesis.replace("\n", " ")
        sentences = [s.strip() for s in flat.split(".") if len(s.strip()) > 20]
        findings = [s + "." for s in sentences[:n]]

    return findings or ["See findings_report.md for the full analysis."]


class DictProxy:
    """Lightweight attribute-access wrapper around a plain dict.

    Used by InsightReporter.from_dicts() to avoid complex nested dataclass
    deserialisation while keeping the class interface clean.
    """

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
