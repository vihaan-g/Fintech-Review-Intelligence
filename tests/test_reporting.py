import os

import pytest


def _realistic_synthesis() -> str:
    return (
        "Finding 1: Jupiter's support volume does not repair trust once account access fails. "
        "Finding 2: PhonePe and Groww absorb UX complaints because core transaction rails stay reliable. "
        "Finding 3: CRED's pain is a rewards-contract issue, not a single technical defect."
    )


def test_insight_reporter_raises_on_empty_synthesis() -> None:
    """InsightReporter raises ValueError if stage3_synthesis is too short."""
    from src.agents.insight_reporter import InsightReporter

    with pytest.raises(ValueError, match="stage3_synthesis"):
        InsightReporter.from_dicts(
            council_dict={"stage3_synthesis": "too short"},
            summary_dict={
                "structured_text": "some text",
                "cross_app_stats": {},
                "high_signal_reviews": [],
            },
        )


def test_insight_reporter_rejects_placeholder_synthesis() -> None:
    """InsightReporter should reject dry-run and repeated-character placeholders."""
    from src.agents.insight_reporter import InsightReporter

    with pytest.raises(ValueError, match="placeholder"):
        InsightReporter.from_dicts(
            council_dict={"stage3_synthesis": "X" * 200},
            summary_dict={
                "structured_text": "some text",
                "cross_app_stats": {},
                "high_signal_reviews": [],
            },
        )

    with pytest.raises(ValueError, match="placeholder"):
        InsightReporter.from_dicts(
            council_dict={
                "stage3_synthesis": (
                    "DRY RUN MOCK: Council synthesis placeholder. "
                    "This is a dry-run output with no real LLM calls."
                )
            },
            summary_dict={
                "structured_text": "some text",
                "cross_app_stats": {},
                "high_signal_reviews": [],
            },
        )


def test_insight_reporter_generates_all_files(tmp_path, monkeypatch) -> None:
    """InsightReporter.generate_all() writes all 3 output files."""
    from src.agents.insight_reporter import InsightReporter

    monkeypatch.chdir(tmp_path)
    os.makedirs("outputs", exist_ok=True)
    reporter = InsightReporter.from_dicts(
        council_dict={
            "stage3_synthesis": _realistic_synthesis(),
            "stage2_gap_analysis": "some gap analysis",
            "generated_at": "2026-04-16T00:00:00",
        },
        summary_dict={
            "structured_text": "## Data Overview\nTestApp: 100 reviews",
            "cross_app_stats": {
                "TestApp": {
                    "total_reviews": 100,
                    "avg_rating": 3.5,
                    "pct_one_star": 10.0,
                    "pct_five_star": 25.0,
                    "reply_rate_pct": 5.0,
                }
            },
            "high_signal_reviews": [],
            "generated_at": "2026-04-16T00:00:00",
        },
    )
    result = reporter.generate_all()
    assert os.path.exists(result.report_path)
    assert os.path.exists(result.linkedin_path)
    assert os.path.exists(result.readme_path)
    assert result.word_count > 0


def test_insight_reporter_uses_live_council_roster_in_outputs(
    tmp_path, monkeypatch
) -> None:
    """Generated outputs should reflect the live council roster and role mapping."""
    from src.agents.insight_reporter import InsightReporter

    monkeypatch.chdir(tmp_path)
    os.makedirs("outputs", exist_ok=True)

    reporter = InsightReporter.from_dicts(
        council_dict={
            "stage3_synthesis": _realistic_synthesis(),
            "stage2_gap_analysis": "some gap analysis",
            "generated_at": "2026-04-20T00:00:00",
        },
        summary_dict={
            "structured_text": "## Data Overview\nTestApp: 100 reviews",
            "cross_app_stats": {
                "TestApp": {
                    "total_reviews": 100,
                    "avg_rating": 3.5,
                    "pct_one_star": 10.0,
                    "pct_five_star": 25.0,
                    "reply_rate_pct": 5.0,
                }
            },
            "high_signal_reviews": [],
            "generated_at": "2026-04-20T00:00:00",
        },
    )
    reporter.generate_all()

    report_text = open("outputs/findings_report.md", encoding="utf-8").read()
    linkedin_text = open("outputs/linkedin_snippet.txt", encoding="utf-8").read()
    readme_text = open("outputs/README.md", encoding="utf-8").read()

    assert "Contrarian Chairman [Gemini 3.1 Pro Preview]" in report_text
    assert "First Principles [Claude Opus 4.7]" in report_text
    assert "Outsider [DeepSeek R1]" in report_text
    assert "Expansionist [Qwen 3.6 Plus]" in report_text
    assert "First Principles [Claude Opus 4.7]" in linkedin_text
    assert "Outsider [DeepSeek R1]" in linkedin_text
    assert "Expansionist [Qwen 3.6 Plus]" in linkedin_text
    assert "Claude Opus 4.7 (First Principles)" in readme_text
    assert "DeepSeek R1 (Outsider)" in readme_text
    assert "Qwen 3.6 Plus (Expansionist)" in readme_text
    assert "Qwen3-235B" not in report_text + linkedin_text + readme_text
    assert "Llama 4 Maverick" not in report_text + linkedin_text + readme_text
    assert "First Principles [DeepSeek R1]" not in report_text + linkedin_text


def test_insight_reporter_prefers_stage2c_audit_synthesis(tmp_path, monkeypatch) -> None:
    """Reporter should prefer the new Stage 2c audit synthesis when present."""
    from src.agents.insight_reporter import InsightReporter

    monkeypatch.chdir(tmp_path)
    os.makedirs("outputs", exist_ok=True)
    reporter = InsightReporter.from_dicts(
        council_dict={
            "stage3_synthesis": _realistic_synthesis(),
            "stage2_gap_analysis": "legacy gap analysis",
            "stage2a_contrarian_pass": "contrarian notes",
            "stage2c_audit_synthesis": "new audit synthesis",
            "stage2b_evidence_audits": {
                "Claude Opus 4.7 [First Principles]": {
                    "clean_response": "audit text"
                }
            },
            "generated_at": "2026-04-20T00:00:00",
        },
        summary_dict={
            "structured_text": "## Data Overview\nTestApp: 100 reviews",
            "cross_app_stats": {
                "TestApp": {
                    "total_reviews": 100,
                    "avg_rating": 3.5,
                    "pct_one_star": 10.0,
                    "pct_five_star": 25.0,
                    "reply_rate_pct": 5.0,
                }
            },
            "high_signal_reviews": [],
            "generated_at": "2026-04-20T00:00:00",
        },
    )
    reporter.generate_all()
    report_text = open("outputs/findings_report.md", encoding="utf-8").read()
    assert "new audit synthesis" in report_text
    assert "legacy gap analysis" not in report_text
    assert "Chairman Contrarian Pass" in report_text
    assert "Evidence Audits" in report_text


def test_format_recovery_hint_for_stage1_abort_has_current_guidance() -> None:
    """Stage 1 recovery hint should match current checkpointing and model setup."""
    from src.main import _format_recovery_hint

    hint = _format_recovery_hint(
        RuntimeError("Stage 1 aborted — 2 member(s) returned empty responses")
    )
    assert "checkpointed" in hint
    assert "OPENROUTER_API_KEY" in hint
    assert "council_stage1_raw.json" not in hint
    assert ":free" not in hint


def test_bug8_report_does_not_claim_full_history(tmp_path, monkeypatch) -> None:
    """Generated report must not claim 'full available history'."""
    from src.agents.insight_reporter import InsightReporter

    monkeypatch.chdir(tmp_path)
    os.makedirs("outputs", exist_ok=True)
    reporter = InsightReporter.from_dicts(
        council_dict={
            "stage3_synthesis": _realistic_synthesis(),
            "stage2_gap_analysis": "gap analysis",
            "generated_at": "2026-04-20T00:00:00",
        },
        summary_dict={
            "structured_text": "## Data Overview\nTestApp: 100 reviews",
            "cross_app_stats": {
                "TestApp": {
                    "total_reviews": 100,
                    "avg_rating": 3.5,
                    "pct_one_star": 10.0,
                    "pct_five_star": 25.0,
                    "reply_rate_pct": 5.0,
                }
            },
            "high_signal_reviews": [],
            "generated_at": "2026-04-20T00:00:00",
        },
    )
    reporter.generate_all()
    report_text = open("outputs/findings_report.md", encoding="utf-8").read()
    assert "full available history" not in report_text
    assert "2,200" in report_text


def test_reporting_copy_mentions_new_council_flow(tmp_path, monkeypatch) -> None:
    """Generated artifacts should describe the revised council flow, not the old one."""
    from src.agents.insight_reporter import InsightReporter

    monkeypatch.chdir(tmp_path)
    os.makedirs("outputs", exist_ok=True)
    reporter = InsightReporter.from_dicts(
        council_dict={
            "stage3_synthesis": _realistic_synthesis(),
            "stage2_gap_analysis": "audit",
            "generated_at": "2026-04-20T00:00:00",
        },
        summary_dict={
            "structured_text": "## Data Overview\nTestApp: 100 reviews",
            "cross_app_stats": {
                "TestApp": {
                    "total_reviews": 100,
                    "avg_rating": 3.5,
                    "pct_one_star": 10.0,
                    "pct_five_star": 25.0,
                    "reply_rate_pct": 5.0,
                }
            },
            "high_signal_reviews": [],
            "generated_at": "2026-04-20T00:00:00",
        },
    )
    reporter.generate_all()
    report_text = open("outputs/findings_report.md", encoding="utf-8").read()
    linkedin_text = open("outputs/linkedin_snippet.txt", encoding="utf-8").read()
    readme_text = open("outputs/README.md", encoding="utf-8").read()
    assert "6-step deliberation" in report_text
    assert "anonymized evidence audits" in report_text
    assert "chairman contrarian review" in linkedin_text
    assert "Stage 2b: Anonymized evidence audits" in readme_text
    assert "Three Tensions gap analysis" not in report_text + linkedin_text + readme_text
