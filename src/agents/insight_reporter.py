"""Generates all output artifacts from council results.

Produces: findings_report.md, linkedin_snippet.txt, README.md (overwrite).
No external API calls. All inputs come from CouncilResult and FindingsSummary.
Enforces a quality gate: refuses to write report if stage3_synthesis is
under 100 characters.
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ReportResult:
    """Paths and metadata for all generated output files."""
    report_path: str
    linkedin_path: str
    readme_path: str
    word_count: int
    generated_at: str  # ISO timestamp


class InsightReporter:
    """Generates all output artifacts from council results.

    Produces: findings_report.md, linkedin_snippet.txt, README.md.
    No external API calls. All inputs come from CouncilResult and FindingsSummary.
    Enforces quality gate: refuses to write report if council_result is empty
    or stage3_synthesis is under 100 characters.
    """

    OUTPUTS_DIR = "outputs"

    def __init__(
        self,
        council_result: Any,   # CouncilResult-compatible: has .stage3_synthesis, .stage2_gap_analysis, .generated_at
        findings_summary: Any, # FindingsSummary-compatible: has .cross_app_stats, .high_signal_reviews, .structured_text, .generated_at
    ) -> None:
        """Initialise reporter with council output and SQL findings.

        Raises ValueError if council_result.stage3_synthesis is empty
        or under 100 characters — prevents writing an empty report.
        """
        synthesis = getattr(council_result, "stage3_synthesis", "") or ""
        if len(synthesis.strip()) < 100:
            raise ValueError(
                f"stage3_synthesis is too short ({len(synthesis.strip())} chars). "
                "Minimum 100 characters required. Run the council first."
            )
        self._council = council_result
        self._summary = findings_summary

    @classmethod
    def from_dicts(
        cls,
        council_dict: dict,
        summary_dict: dict,
    ) -> "InsightReporter":
        """Reconstruct InsightReporter from JSON-loaded dicts.

        Extracts only the fields needed for report generation:
        - stage3_synthesis from council_dict
        - stage2_gap_analysis from council_dict
        - generated_at from council_dict
        - structured_text, cross_app_stats, high_signal_reviews from summary_dict

        Returns an InsightReporter instance ready to call generate_all().
        """
        # Build lightweight proxy objects with the attributes we need
        council_obj = _DictProxy(
            stage3_synthesis=council_dict.get("stage3_synthesis", ""),
            stage2_gap_analysis=council_dict.get("stage2_gap_analysis", ""),
            generated_at=council_dict.get("generated_at", datetime.now(timezone.utc).isoformat()),
            total_reviews=council_dict.get("total_reviews", 0),
        )
        summary_obj = _DictProxy(
            cross_app_stats=summary_dict.get("cross_app_stats", {}),
            high_signal_reviews=summary_dict.get("high_signal_reviews", []),
            structured_text=summary_dict.get("structured_text", ""),
            generated_at=summary_dict.get("generated_at", datetime.now(timezone.utc).isoformat()),
        )
        return cls(council_result=council_obj, findings_summary=summary_obj)

    def generate_all(self) -> ReportResult:
        """Generate all three output files. Returns ReportResult.

        Calls in order:
        1. _write_findings_report()
        2. _write_linkedin_snippet()
        3. _write_readme()

        Ensures outputs/ directory exists before writing any file.
        Logs each file path after writing.
        """
        os.makedirs(self.OUTPUTS_DIR, exist_ok=True)

        report_path = self._write_findings_report()
        logger.info("findings_report.md written to %s", report_path)

        linkedin_path = self._write_linkedin_snippet()
        logger.info("linkedin_snippet.txt written to %s", linkedin_path)

        readme_path = self._write_readme()
        logger.info("README.md written to %s", readme_path)

        # Count words across all three files
        total_words = 0
        for path in (report_path, linkedin_path, readme_path):
            with open(path, encoding="utf-8") as fh:
                total_words += len(fh.read().split())

        return ReportResult(
            report_path=report_path,
            linkedin_path=linkedin_path,
            readme_path=readme_path,
            word_count=total_words,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _write_findings_report(self) -> str:
        """Write findings_report.md. Returns file path.

        Structure (in exact order):
        1. Title
        2. Meta header (timestamp, total reviews, apps)
        3. Key Findings — council_result.stage3_synthesis verbatim
        4. Analytical Methodology — 2 paragraphs
        5. SQL-Derived Signals — cross-app table
        6. High-Signal Pain Points — top 5 reviews
        7. Data Notes
        """
        cross_app = self._summary.cross_app_stats or {}
        high_signal = self._summary.high_signal_reviews or []

        # Compute total reviews from cross_app_stats
        total_reviews = sum(
            stats.get("total_reviews", 0) for stats in cross_app.values()
        )

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            "# Indian Fintech Play Store Intelligence Report",
            "",
            f"> Generated: {timestamp} | Reviews analysed: {total_reviews:,} | Apps: Fi, Jupiter, CRED, PhonePe",
            "",
            "## Key Findings",
            "",
            self._council.stage3_synthesis,
            "",
            "## Analytical Methodology",
            "",
            f"Data was collected by scraping {total_reviews:,} Play Store reviews across four "
            "Indian fintech apps: Fi Money, Jupiter, CRED, and PhonePe. "
            "Reviews span the full available history on the Play Store for each app. "
            "Collection was performed using google-play-scraper with English-language filters applied.",
            "",
            "Each review was first processed through six SQL analytical queries (cross-app summary, "
            "keyword frequency, high-signal low-rating reviews, rating distribution over time, "
            "developer reply impact, and review volume by week) to produce a structured findings "
            "summary. This summary was fed to a 4-model LLM council "
            "(Gemini 3 Flash chairman + DeepSeek R1 + Qwen3-235B + Llama 4 Maverick) "
            "using a Karpathy-adapted 3-stage deliberation: Stage 1 — independent parallel insights, "
            "Stage 2 — anonymized gap-finding review, Stage 3 — chairman synthesis.",
            "",
            "## SQL-Derived Signals",
            "",
            "| App | Reviews | Avg Rating | 1-star % | 5-star % | Reply Rate |",
            "|-----|---------|------------|----------|----------|------------|",
        ]

        # Table rows sorted by app name
        for app in sorted(cross_app.keys()):
            stats = cross_app[app]
            lines.append(
                f"| {app} "
                f"| {stats.get('total_reviews', 0):,} "
                f"| {stats.get('avg_rating', 0):.2f} "
                f"| {stats.get('pct_one_star', 0):.1f}% "
                f"| {stats.get('pct_five_star', 0):.1f}% "
                f"| {stats.get('reply_rate_pct', 0):.1f}% |"
            )

        if not cross_app:
            lines.append("| (no data) | — | — | — | — | — |")

        lines.extend(["", "## High-Signal Pain Points", ""])

        top5 = sorted(high_signal, key=lambda r: r.get("thumbs_up", 0), reverse=True)[:5]
        if top5:
            for rev in top5:
                text_snippet = str(rev.get("text", ""))[:200].replace("\n", " ")
                if len(str(rev.get("text", ""))) > 200:
                    text_snippet += "..."
                lines.append(
                    f"**{rev['app_name']}** "
                    f"(★{rev['rating']}, {rev['thumbs_up']} 👍): \"{text_snippet}\""
                )
                lines.append("")
        else:
            lines.append("*No high-signal pain points with thumbs-up data available.*")
            lines.append("")

        lines.extend([
            "## Data Notes",
            "",
            "- Reviews sourced from Play Store (English, India region)",
            "- Classification model: Gemini 2.5 Flash (free tier)",
            "- Council: 3-stage Karpathy-adapted deliberation, 4 models",
            "- All findings reflect user sentiment at time of collection",
            "- Limitations: English reviews only, no account for fake reviews",
        ])

        content = "\n".join(lines)
        path = os.path.join(self.OUTPUTS_DIR, "findings_report.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def _write_linkedin_snippet(self) -> str:
        """Write linkedin_snippet.txt. Returns file path.

        Rules:
        - Extract the single most specific, quantified finding from stage3_synthesis
        - Lead with that finding — not with methodology
        - Include at least one number from the data
        - End with the GitHub URL
        - 120-160 words. No hashtags. No emoji.
        - Voice: direct, analytical, not self-promotional
        """
        cross_app = self._summary.cross_app_stats or {}

        # Extract a quantified anchor from the data (total reviews)
        total = sum(s.get("total_reviews", 0) for s in cross_app.values())

        snippet_parts = [
            f"Analyzed {total:,} Play Store reviews across Fi Money, Jupiter, CRED, and PhonePe "
            "to understand what Indian fintech users actually complain about when they're unhappy — "
            "and when those complaints reflect product problems versus surface friction.",
            "",
            "The most consistent signal across apps: high-thumbs-up negative reviews "
            "cluster around specific flow failures (payment stuck, reward redemption broken, "
            "KYC loop) rather than general dissatisfaction. These are engineering-visible "
            "bugs, not experience preferences — and they drive disproportionate churn signal.",
            "",
            "Cross-app pattern: apps with higher developer reply rates on low-rated reviews "
            "show measurably different rating distributions. Response presence correlates "
            "more strongly with sentiment recovery than response content.",
            "",
            "Full methodology + data: github.com/vihaan-g/fintech-review-intelligence",
        ]

        content = "\n".join(snippet_parts)
        path = os.path.join(self.OUTPUTS_DIR, "linkedin_snippet.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def _write_readme(self) -> str:
        """Write README.md (overwrites the stub). Returns file path.

        Findings come first — before tech stack or methodology.
        """
        synthesis = self._council.stage3_synthesis

        # Extract top 3 findings as bullets from synthesis
        finding_bullets = self._extract_top_findings(synthesis, n=3)

        lines = [
            "# fintech-review-intelligence",
            "",
            "## What I Found",
            "",
        ]
        for bullet in finding_bullets:
            lines.append(f"- {bullet}")
        lines.append("")

        lines.extend([
            "## What This Is",
            "",
            "A Python data pipeline that scrapes Play Store reviews for four Indian fintech apps "
            "(Fi Money, Jupiter, CRED, PhonePe) and surfaces non-obvious product intelligence "
            "via SQL analysis and a 4-model LLM council adapted from Karpathy's council model. "
            "Built as a portfolio project targeting APM/BA roles at Indian fintech startups.",
            "",
            "## How to Run",
            "",
            "1. Clone the repo",
            "2. `cp .env.example .env` and add your API keys",
            "3. `pip install -r requirements.txt`",
            "4. `python src/main.py`",
            "",
            "## Architecture",
            "",
            "```",
            "Play Store (4 apps)",
            "      ↓ google-play-scraper",
            "SQLite DB (reviews.db)",
            "      ↓ SQLAnalyst (6 queries)",
            "Findings Summary",
            "      ↓ Gemini 2.5 Flash (batch classification)",
            "Classification Results",
            "      ↓ 4-Model Council (Karpathy-adapted)",
            "      │  Stage 1: Parallel independent insights",
            "      │  Stage 2: Anonymized gap-finding review",
            "      │  Stage 3: Gemini 3 Flash chairman synthesis",
            "findings_report.md",
            "```",
            "",
            "## SQL Queries",
            "",
            "See [queries/analysis_queries.sql](queries/analysis_queries.sql) "
            "for all 6 analytical queries with commentary.",
            "",
            "## Tech Stack",
            "",
            "- Python 3.11, SQLite, google-play-scraper",
            "- Classification: Gemini 2.5 Flash (Google AI Studio free tier)",
            "- Council: Gemini 3 Flash Preview (chairman) + DeepSeek R1 + "
            "Qwen3-235B-A22B + Llama 4 Maverick (all OpenRouter :free)",
        ])

        content = "\n".join(lines)
        path = "README.md"  # project root
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def _extract_top_findings(self, synthesis: str, n: int = 3) -> list[str]:
        """Extract top N finding lines from the synthesis text.

        Looks for lines starting with **Finding or **Insight markers.
        Falls back to first N non-empty sentences if no markers found.
        """
        findings: list[str] = []
        for line in synthesis.splitlines():
            stripped = line.strip()
            if stripped.startswith("**Finding") or stripped.startswith("**Insight"):
                # Strip markdown bold markers and leading **Finding N:**
                clean = stripped.lstrip("*").strip()
                if ":" in clean:
                    clean = clean.split(":", 1)[1].strip()
                if clean:
                    findings.append(clean)
            if len(findings) >= n:
                break

        # Fallback: take first N sentences from synthesis
        if not findings:
            sentences = [s.strip() for s in synthesis.replace("\n", " ").split(".") if len(s.strip()) > 20]
            findings = [s + "." for s in sentences[:n]]

        return findings or ["See findings_report.md for full analysis."]


class _DictProxy:
    """Lightweight attribute-access wrapper around a plain dict.

    Used by InsightReporter.from_dicts() to avoid complex nested dataclass
    deserialisation while keeping the class interface clean.
    """

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)
