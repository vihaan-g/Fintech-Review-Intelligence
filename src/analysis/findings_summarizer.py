"""Synthesizes SQLAnalyst results into a structured summary for the LLM council."""
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

from src.analysis.sql_analyst import SQLAnalyst

logger = logging.getLogger(__name__)


@dataclass
class FindingsSummary:
    """Structured output from FindingsSummarizer."""

    cross_app_stats: dict
    high_signal_reviews: list[dict]
    keyword_frequencies: dict
    rating_trends: list[dict]
    developer_reply_impact: dict
    volume_spikes: list[dict]
    structured_text: str
    generated_at: str


class FindingsSummarizer:
    """Synthesizes SQLAnalyst query results into a structured summary
    that serves as the input to the LLM council.

    The summary must be specific and data-driven — not generic observations.
    """

    def __init__(self, analyst: SQLAnalyst) -> None:
        """Initialise with an SQLAnalyst bound to an open DatabaseManager.

        Args:
            analyst: Configured SQLAnalyst instance.
        """
        self._analyst = analyst

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    def generate_summary(self) -> FindingsSummary:
        """Run all queries and compile into FindingsSummary.

        structured_text format:

        ## Data Overview
        [total reviews per app, date range, collection timestamp]

        ## Cross-App Patterns
        [2-3 observations across multiple apps with specific metrics and numbers]

        ## App-Specific Signals
        ### Groww / Jupiter / CRED / PhonePe / Paytm
        [2-3 bullets with specific numbers per app]

        ## High-Signal Pain Points (validated by other users)
        [Top 5 reviews by thumbs_up with rating <= 2]

        ## Developer Response Patterns
        [Reply rates per app and rating correlation observations]

        Total length: 400-600 words. Every claim is tied to a number.

        Returns:
            FindingsSummary dataclass with all fields populated.
        """
        logger.info("Generating findings summary — running all queries.")

        cross_app = self._analyst.cross_app_summary()
        high_signal = self._analyst.high_signal_low_rating_reviews(min_thumbs=1)
        keywords = self._analyst.keyword_frequency()
        rating_trends = self._analyst.rating_distribution_over_time()
        reply_impact = self._analyst.developer_reply_impact()
        volume_spikes = self._analyst.review_volume_by_week()

        structured = self._build_structured_text(
            cross_app=cross_app,
            high_signal=high_signal,
            keywords=keywords,
            rating_trends=rating_trends,
            reply_impact=reply_impact,
        )

        return FindingsSummary(
            cross_app_stats=cross_app,
            high_signal_reviews=high_signal,
            keyword_frequencies=keywords,
            rating_trends=rating_trends,
            developer_reply_impact=reply_impact,
            volume_spikes=volume_spikes,
            structured_text=structured,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

    def save_to_file(
        self,
        summary: FindingsSummary,
        path: str = "outputs/findings_summary.json",
    ) -> None:
        """Serialize FindingsSummary to JSON and save to outputs/.

        Args:
            summary: The FindingsSummary to persist.
            path:    Destination file path.

        Raises:
            OSError: If the directory cannot be created or the file cannot be written.
        """
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

            payload = {
                "generated_at": summary.generated_at,
                "cross_app_stats": summary.cross_app_stats,
                "high_signal_reviews": summary.high_signal_reviews,
                "keyword_frequencies": summary.keyword_frequencies,
                "rating_trends": summary.rating_trends,
                "developer_reply_impact": summary.developer_reply_impact,
                "volume_spikes": summary.volume_spikes,
                "structured_text": summary.structured_text,
            }
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)

            logger.info("Findings summary saved to %s", path)
        except OSError as exc:
            logger.error("Failed to save findings summary to %s: %s", path, exc)
            raise

    def enrich_with_classification(
        self,
        path: str = "outputs/findings_summary.json",
    ) -> bool:
        """Enrich findings_summary.json with classification signals.

        Reads the file at path, adds classification_breakdown and
        top_classified_complaints keys, appends two new sections to
        structured_text, then writes the enriched dict back.

        Returns True if enriched, False if skipped (0 classified reviews).

        Args:
            path: Path to findings_summary.json (default outputs/).

        Raises:
            OSError: If the file cannot be read or written.
            json.JSONDecodeError: If the file is not valid JSON.
        """
        breakdown = self._analyst.classification_breakdown()
        complaints = self._analyst.top_classified_complaints()

        if not breakdown and not complaints:
            logger.warning(
                "No classified reviews found — skipping classification enrichment."
            )
            return False

        try:
            with open(path, encoding="utf-8") as fh:
                summary = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.error("Could not read %s for enrichment: %s", path, exc)
            raise

        summary["classification_breakdown"] = breakdown
        summary["top_classified_complaints"] = complaints

        classification_section = self._build_classification_text(breakdown, complaints)
        existing_text = summary.get("structured_text", "")
        # Strip any previously appended classification section so repeated calls
        # (across partial/resumed runs) replace rather than accumulate.
        markers = [
            "\n\n## Complaint Category Over-Indexing",
            "\n\n## Classification Signals",
            "\n\n## Top Classified Pain Points",
        ]
        marker_positions = [
            existing_text.find(marker)
            for marker in markers
            if existing_text.find(marker) != -1
        ]
        base_text = (
            existing_text[: min(marker_positions)]
            if marker_positions
            else existing_text
        )
        summary["structured_text"] = base_text + "\n\n" + classification_section

        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(summary, fh, indent=2, ensure_ascii=False)
            logger.info("findings_summary.json enriched with classification signals.")
        except OSError as exc:
            logger.error("Failed to write enriched findings_summary to %s: %s", path, exc)
            raise

        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_classification_text(
        self,
        breakdown: dict[str, dict],
        complaints: list[dict],
    ) -> str:
        """Build the Classification Signals and Top Classified Pain Points sections.

        Args:
            breakdown:  Output of classification_breakdown().
            complaints: Output of top_classified_complaints().

        Returns:
            Formatted multi-section string to append to structured_text.
        """
        sections: list[str] = []
        over_index_lines = self._classification_over_index_lines(breakdown)
        if over_index_lines:
            sections.append("\n".join(["## Complaint Category Over-Indexing", *over_index_lines]))

        signals_lines = ["## Classification Signals"]
        for app, areas in sorted(breakdown.items()):
            signals_lines.append(f"### {app}")
            sorted_areas = sorted(
                areas.items(),
                key=lambda kv: kv[1]["count"],
                reverse=True,
            )
            for area, stats in sorted_areas:
                signals_lines.append(
                    f"- {area}: {stats['count']} reviews "
                    f"({stats['pct_of_low_rated']}% of low-rated)"
                )
        sections.append("\n".join(signals_lines))

        if complaints:
            pain_lines = ["## Top Classified Pain Points"]
            for c in complaints:
                snippet = str(c.get("text", ""))[:120]
                pain_lines.append(
                    f"- [{c['app_name']} / {c['product_area']}] "
                    f"\u2605{c['rating']}, {c['thumbs_up']}\U0001f44d: \"{snippet}...\""
                )
            sections.append("\n".join(pain_lines))

        return "\n\n".join(sections)

    def _build_structured_text(
        self,
        cross_app: dict,
        high_signal: list[dict],
        keywords: dict,
        rating_trends: list[dict],
        reply_impact: dict,
    ) -> str:
        """Construct the structured narrative text from query results.

        Args:
            cross_app:      Output of cross_app_summary().
            high_signal:    Output of high_signal_low_rating_reviews().
            keywords:       Output of keyword_frequency().
            rating_trends:  Output of rating_distribution_over_time().
            reply_impact:   Output of developer_reply_impact().

        Returns:
            Formatted multi-section string, 400-600 words.
        """
        sections: list[str] = []
        incident_lines = self._incident_signal_lines(cross_app, rating_trends, self._top_incident_candidates())

        # -- Data Overview -------------------------------------------
        overview_lines = ["## Data Overview"]
        if cross_app:
            for app, stats in sorted(cross_app.items()):
                overview_lines.append(
                    f"- {app}: {stats['total_reviews']} reviews, "
                    f"avg rating {stats['avg_rating']}"
                )
        else:
            overview_lines.append("- No app data available.")
        sections.append("\n".join(overview_lines))

        # -- Cross-App Patterns ---------------------------------------
        patterns_lines = ["## Cross-App Patterns"]
        patterns_lines.extend(self._cross_app_pattern_bullets(cross_app, keywords))
        if incident_lines:
            patterns_lines.extend(incident_lines[:2])
        sections.append("\n".join(patterns_lines))

        # -- App-Specific Signals -------------------------------------
        signals_lines = ["## App-Specific Signals"]
        target_apps = ["Groww", "Jupiter", "CRED", "PhonePe", "Paytm"]
        all_apps = sorted(cross_app.keys())
        display_apps = target_apps if any(a in cross_app for a in target_apps) else all_apps

        for app in display_apps:
            if app not in cross_app:
                continue
            stats = cross_app[app]
            signals_lines.append(f"### {app}")
            signals_lines.append(
                f"- {stats['total_reviews']} total reviews; "
                f"{stats['pct_one_star']}% one-star, "
                f"{stats['pct_five_star']}% five-star."
            )
            signals_lines.append(
                f"- Most common rating: {stats['most_common_rating']}; "
                f"developer reply rate: {stats['reply_rate_pct']}%."
            )
            keyword_callout = self._top_keyword_signal_for_app(app, cross_app, keywords)
            if keyword_callout:
                signals_lines.append(f"- {keyword_callout}")
            incident_callout = self._top_incident_for_app(app, cross_app, rating_trends)
            if incident_callout:
                signals_lines.append(f"- {incident_callout}")

        sections.append("\n".join(signals_lines))

        # -- High-Signal Pain Points ----------------------------------
        pain_lines = ["## High-Signal Pain Points (validated by other users)"]
        top_reviews = sorted(high_signal, key=lambda r: r.get("thumbs_up", 0), reverse=True)[:5]
        if top_reviews:
            for rev in top_reviews:
                snippet = str(rev.get("text", ""))[:150].replace("\n", " ")
                pain_lines.append(
                    f"- [{rev['app_name']}] {rev['rating']}/5 stars, "
                    f"{rev['thumbs_up']} thumbs up: \"{snippet}\""
                )
        else:
            pain_lines.append("- No high-signal low-rating reviews found.")
        sections.append("\n".join(pain_lines))

        # -- Developer Response Patterns ------------------------------
        reply_lines = ["## Developer Response Patterns"]
        if reply_impact:
            for app, data in sorted(reply_impact.items()):
                reply_lines.append(
                    f"- {app}: {data['reply_rate_pct']}% reply rate on low-rated reviews "
                    f"({data['replied_count']}/{data['total_low_ratings']}). "
                    f"Low-rated reviews that received a reply averaged {data['avg_rating_with_reply']} vs "
                    f"{data['avg_rating_without_reply']} without a reply; treat this as response behavior, "
                    "not evidence that replies caused rating changes."
                )
        else:
            reply_lines.append("- No low-rated reviews with reply data found.")
        sections.append("\n".join(reply_lines))

        return "\n\n".join(sections)

    def _top_incident_candidates(self) -> list[dict]:
        """Return incident-like weekly anomalies using simple app-relative thresholds."""
        weekly_rows = self._analyst.review_volume_by_week()
        by_app: dict[str, list[dict]] = {}
        for row in weekly_rows:
            by_app.setdefault(str(row["app_name"]), []).append(row)

        candidates: list[dict] = []
        for app, rows in by_app.items():
            volumes = [int(row["review_count"]) for row in rows]
            ratings = [float(row["avg_rating"] or 0.0) for row in rows if row.get("avg_rating") is not None]
            if len(volumes) < 3 or not ratings:
                continue

            baseline_volume = sum(volumes) / len(volumes)
            baseline_rating = sum(ratings) / len(ratings)
            for row in rows:
                review_count = int(row["review_count"])
                avg_rating = float(row["avg_rating"] or 0.0)
                if baseline_volume < 1:
                    continue
                if review_count < max(20, int(baseline_volume * 1.5)):
                    continue
                if avg_rating > baseline_rating - 0.4:
                    continue
                candidates.append(
                    {
                        "app_name": app,
                        "week": str(row["week"]),
                        "review_count": review_count,
                        "avg_rating": avg_rating,
                        "baseline_volume": round(baseline_volume, 1),
                        "baseline_rating": round(baseline_rating, 2),
                        "volume_lift": round(review_count / baseline_volume, 1),
                        "rating_drop": round(baseline_rating - avg_rating, 2),
                    }
                )

        return sorted(
            candidates,
            key=lambda row: (row["rating_drop"], row["volume_lift"]),
            reverse=True,
        )

    def _incident_signal_lines(
        self,
        cross_app: dict,
        rating_trends: list[dict],
        incidents: list[dict],
    ) -> list[str]:
        """Build concise incident-like bullets for the strongest anomalies."""
        if not cross_app or not rating_trends or not incidents:
            return []

        lines: list[str] = []
        for incident in incidents[:2]:
            lines.append(
                f"- Incident-like signal: {incident['app_name']} saw {incident['review_count']} reviews in week "
                f"{incident['week']} ({incident['volume_lift']}x its weekly baseline) while avg rating fell to "
                f"{incident['avg_rating']} from a {incident['baseline_rating']} baseline."
            )
        return lines

    def _top_keyword_signal_for_app(
        self,
        app: str,
        cross_app: dict,
        keywords: dict,
    ) -> str:
        """Return the strongest normalized keyword signal for an app."""
        total_reviews = cross_app.get(app, {}).get("total_reviews", 0)
        if not total_reviews:
            return ""

        best_keyword = ""
        best_count = 0
        best_rate = 0.0
        for keyword, counts in keywords.items():
            mention_count = int(counts.get(app, 0))
            if mention_count <= 0:
                continue
            mention_rate = (mention_count / total_reviews) * 100
            if mention_rate > best_rate:
                best_keyword = keyword
                best_count = mention_count
                best_rate = mention_rate

        if not best_keyword:
            return ""
        return (
            f"Strongest keyword signal: '{best_keyword}' appears {best_count} times "
            f"({best_rate:.1f} mentions per 100 reviews)."
        )

    def _top_incident_for_app(
        self,
        app: str,
        cross_app: dict,
        rating_trends: list[dict],
    ) -> str:
        """Return one incident-like sentence for the app if a strong anomaly exists."""
        del cross_app, rating_trends
        for incident in self._top_incident_candidates():
            if incident["app_name"] == app:
                return (
                    f"Strongest weekly anomaly: week {incident['week']} reached {incident['review_count']} reviews "
                    f"({incident['volume_lift']}x baseline) with avg rating at {incident['avg_rating']}."
                )
        return ""

    def _classification_over_index_lines(self, breakdown: dict[str, dict]) -> list[str]:
        """Return complaint categories that materially over-index by app."""
        totals_by_area: dict[str, int] = {}
        totals_by_app: dict[str, int] = {}
        overall_total = 0

        for app, areas in breakdown.items():
            app_total = sum(int(stats["count"]) for stats in areas.values())
            totals_by_app[app] = app_total
            overall_total += app_total
            for area, stats in areas.items():
                totals_by_area[area] = totals_by_area.get(area, 0) + int(stats["count"])

        if overall_total == 0:
            return []

        lines: list[str] = []
        for app, areas in breakdown.items():
            app_total = totals_by_app.get(app, 0)
            if app_total < 5:
                continue
            strongest: tuple[str, float, float] | None = None
            for area, stats in areas.items():
                area_total = totals_by_area.get(area, 0)
                if area_total < 5:
                    continue
                app_share = int(stats["count"]) / app_total
                baseline_share = area_total / overall_total
                if app_share <= baseline_share * 1.5:
                    continue
                if strongest is None or (app_share - baseline_share) > (strongest[1] - strongest[2]):
                    strongest = (area, app_share, baseline_share)
            if strongest is None:
                continue
            area, app_share, baseline_share = strongest
            lines.append(
                f"- {app} over-indexes on {area}: {app_share * 100:.1f}% of its classified low-rated reviews vs "
                f"{baseline_share * 100:.1f}% across the combined app set."
            )

        return lines

    def _cross_app_pattern_bullets(
        self, cross_app: dict, keywords: dict
    ) -> list[str]:
        """Generate 2-3 cross-app pattern bullets grounded in data.

        Args:
            cross_app: Output of cross_app_summary().
            keywords:  Output of keyword_frequency().

        Returns:
            List of bullet strings.
        """
        bullets: list[str] = []

        if cross_app:
            avg_ratings = {app: s["avg_rating"] for app, s in cross_app.items()}
            if avg_ratings:
                best_app = max(avg_ratings, key=lambda a: avg_ratings[a])
                worst_app = min(avg_ratings, key=lambda a: avg_ratings[a])
                if best_app != worst_app:
                    bullets.append(
                        f"- Rating gap: {best_app} leads at {avg_ratings[best_app]} avg "
                        f"vs {worst_app} at {avg_ratings[worst_app]} avg."
                    )

            reply_rates = {
                app: s["reply_rate_pct"]
                for app, s in cross_app.items()
                if s["reply_rate_pct"] is not None
            }
            if reply_rates:
                most_responsive = max(reply_rates, key=lambda a: reply_rates[a])
                bullets.append(
                    f"- Developer responsiveness: {most_responsive} has the highest "
                    f"reply rate at {reply_rates[most_responsive]}% on all reviews."
                )

        if keywords:
            kw_totals = {
                kw: sum(counts.values()) for kw, counts in keywords.items()
            }
            if kw_totals:
                top_kw = max(kw_totals, key=lambda k: kw_totals[k])
                bullets.append(
                    f"- Most mentioned keyword across all apps: '{top_kw}' "
                    f"({kw_totals[top_kw]} mentions total)."
                )

        if not bullets:
            bullets.append("- Insufficient data for cross-app pattern analysis.")

        return bullets
