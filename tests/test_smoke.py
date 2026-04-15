import os

import pytest

from src.config import Config
from src.data_collection.database_manager import DatabaseManager
from src.analysis.sql_analyst import SQLAnalyst
from src.analysis.findings_summarizer import FindingsSummarizer

# Ensure outputs/ exists before any test that writes to it
os.makedirs("outputs", exist_ok=True)


def test_project_structure():
    """Confirms the project skeleton was created correctly."""
    assert os.path.exists("src/config.py")
    assert os.path.exists("src/data_collection/database_manager.py")
    assert os.path.exists("src/council/council_orchestrator.py")
    assert os.path.exists("src/agents/insight_reporter.py")
    assert os.path.exists(".claude/skills/prompt-optimizer/SKILL.md")
    assert os.path.exists(".claude/skills/multi-agent-patterns/SKILL.md")
    assert os.path.exists(".claude/skills/write-judge-prompt/SKILL.md")
    assert os.path.exists(".claude/agents/council-orchestrator.md")


def test_config_raises_on_missing_keys(monkeypatch):
    """Config.from_env() should raise ValueError listing all missing keys."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Missing required environment variables"):
        Config.from_env()


def test_database_manager_schema_and_insert():
    """DatabaseManager creates schema and inserts reviews correctly."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        fake_reviews = [
            {
                "app_name": "TestApp",
                "review_id": "r1",
                "rating": 4,
                "text": "Great app",
                "date": "2026-01-01T00:00:00",
                "thumbs_up": 5,
                "has_dev_reply": 0,
                "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
                "classification": None,
            }
        ]
        inserted = db.insert_reviews(fake_reviews)
        assert inserted == 1
        assert db.get_review_count("TestApp") == 1


def test_pipeline_state_checkpoint():
    """DatabaseManager saves and retrieves phase state correctly."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.save_phase_state("collection", "complete", {"count": 100})
        state = db.get_phase_state("collection")
        assert state["status"] == "complete"


def test_sql_analyst_methods_return_correct_types():
    """SQLAnalyst methods return expected types on a seeded database."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        # Seed with minimal data
        reviews = [
            {
                "app_name": "TestApp",
                "review_id": f"r{i}",
                "rating": i % 5 + 1,
                "text": f"review text {i} upi cashback",
                "date": "2026-01-15T00:00:00",
                "thumbs_up": i * 2,
                "has_dev_reply": i % 2,
                "dev_reply_text": "Thanks" if i % 2 else None,
                "scraped_at": "2026-04-15T00:00:00",
                "classification": None,
            }
            for i in range(10)
        ]
        db.insert_reviews(reviews)
        analyst = SQLAnalyst(db)

        assert isinstance(analyst.cross_app_summary(), dict)
        assert isinstance(analyst.keyword_frequency(["upi", "cashback"]), dict)
        assert isinstance(analyst.high_signal_low_rating_reviews(min_thumbs=0), list)
        assert isinstance(analyst.rating_distribution_over_time(), list)


def test_findings_summarizer_generates_structured_text():
    """FindingsSummarizer.generate_summary() returns a FindingsSummary
    with non-empty structured_text."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        reviews = [
            {
                "app_name": "TestApp",
                "review_id": f"r{i}",
                "rating": (i % 5) + 1,
                "text": f"test review {i}",
                "date": "2026-01-15T00:00:00",
                "thumbs_up": i,
                "has_dev_reply": 0,
                "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
                "classification": None,
            }
            for i in range(20)
        ]
        db.insert_reviews(reviews)
        analyst = SQLAnalyst(db)
        summarizer = FindingsSummarizer(analyst)
        summary = summarizer.generate_summary()

        assert isinstance(summary.structured_text, str)
        assert len(summary.structured_text) > 100
        assert "TestApp" in summary.structured_text


def test_developer_reply_impact_handles_no_replies():
    """developer_reply_impact() returns 0.0 for avg_rating_with_reply
    when no reviews have dev replies — does not raise TypeError."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        reviews = [
            {
                "app_name": "TestApp",
                "review_id": f"r{i}",
                "rating": 1,
                "text": "bad app",
                "date": "2026-01-15T00:00:00",
                "thumbs_up": 0,
                "has_dev_reply": 0,
                "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
            }
            for i in range(5)
        ]
        db.insert_reviews(reviews)
        analyst = SQLAnalyst(db)
        result = analyst.developer_reply_impact()
        assert "TestApp" in result
        assert result["TestApp"]["avg_rating_with_reply"] == 0.0
        assert result["TestApp"]["reply_rate_pct"] == 0.0


def test_keyword_frequency_returns_empty_dict_on_no_matches():
    """keyword_frequency() returns empty dict when no reviews match."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([{
            "app_name": "TestApp",
            "review_id": "r1",
            "rating": 5,
            "text": "great app",
            "date": "2026-01-15T00:00:00",
            "thumbs_up": 0,
            "has_dev_reply": 0,
            "dev_reply_text": None,
            "scraped_at": "2026-04-15T00:00:00",
        }])
        analyst = SQLAnalyst(db)
        result = analyst.keyword_frequency(["zzznomatch"])
        assert result == {}
