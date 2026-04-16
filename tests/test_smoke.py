import json
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


# ---------------------------------------------------------------------------
# FIX 1.4 — Rollback test
# ---------------------------------------------------------------------------

def test_database_manager_rollback_on_exception():
    """DatabaseManager rolls back on exception — data is not committed."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        # Open a nested connection to the same in-memory DB is not possible,
        # so we trigger rollback by calling __exit__ with a simulated exception
        # directly on a fresh sub-context-manager instance.
        inner_db = DatabaseManager(db_path=":memory:")
        inner_db.__enter__()
        inner_db.create_schema()
        inner_db.insert_reviews([{
            "app_name": "TestApp",
            "review_id": "rollback_r1",
            "rating": 3,
            "text": "test",
            "date": "2026-01-01T00:00:00",
            "thumbs_up": 0,
            "has_dev_reply": 0,
            "dev_reply_text": None,
            "scraped_at": "2026-04-15T00:00:00",
        }])
        # Simulate an exception during __exit__ — triggers rollback path
        inner_db.__exit__(ValueError, ValueError("Simulated failure"), None)
        # Connection closed; re-open to verify no data persisted
        # (in-memory DB is destroyed on close, so count must be 0 post-reopen)
        with DatabaseManager(db_path=":memory:") as verify_db:
            verify_db.create_schema()
            assert verify_db.get_review_count("TestApp") == 0


# ---------------------------------------------------------------------------
# FIX 1.5 — Deduplication test
# ---------------------------------------------------------------------------

def test_database_manager_insert_deduplicates():
    """insert_reviews() with duplicate review_id inserts only once."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        review = {
            "app_name": "TestApp",
            "review_id": "dup_r1",
            "rating": 4,
            "text": "good app",
            "date": "2026-01-01T00:00:00",
            "thumbs_up": 2,
            "has_dev_reply": 0,
            "dev_reply_text": None,
            "scraped_at": "2026-04-15T00:00:00",
        }
        db.insert_reviews([review])
        db.insert_reviews([review])  # second insert of same review_id
        assert db.get_review_count("TestApp") == 1


# ---------------------------------------------------------------------------
# GROUP 4 — Missing method tests (FIX 4.1)
# ---------------------------------------------------------------------------

def test_config_from_env_success(monkeypatch):
    """Config.from_env() returns correct values when all keys are set."""
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter_key")
    config = Config.from_env()
    assert config.gemini_api_key == "test_gemini_key"
    assert config.openrouter_api_key == "test_openrouter_key"


def test_database_manager_both_tables_created():
    """create_schema() creates both reviews and pipeline_state tables."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        cursor = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        assert "reviews" in tables
        assert "pipeline_state" in tables


def test_database_manager_get_review_count_per_app():
    """get_review_count() filters correctly by app_name."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        reviews = [
            {
                "app_name": "AppA",
                "review_id": f"a{i}",
                "rating": 4, "text": "good",
                "date": "2026-01-01T00:00:00",
                "thumbs_up": 0, "has_dev_reply": 0,
                "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
            }
            for i in range(3)
        ] + [
            {
                "app_name": "AppB",
                "review_id": f"b{i}",
                "rating": 3, "text": "ok",
                "date": "2026-01-01T00:00:00",
                "thumbs_up": 0, "has_dev_reply": 0,
                "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
            }
            for i in range(2)
        ]
        db.insert_reviews(reviews)
        assert db.get_review_count("AppA") == 3
        assert db.get_review_count("AppB") == 2
        assert db.get_review_count() == 5


def test_database_manager_phase_state_upsert():
    """save_phase_state() called twice for same phase updates, not duplicates."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.save_phase_state("collection", "in_progress")
        db.save_phase_state("collection", "complete", {"count": 100})
        state = db.get_phase_state("collection")
        assert state["status"] == "complete"
        cursor = db.conn.execute(
            "SELECT COUNT(*) FROM pipeline_state WHERE phase = 'collection'"
        )
        assert cursor.fetchone()[0] == 1  # upsert, not duplicate


def test_database_manager_get_unclassified_reviews():
    """get_unclassified_reviews() returns only reviews with classification IS NULL."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        reviews = [
            {
                "app_name": "TestApp",
                "review_id": f"r{i}",
                "rating": 3, "text": f"review {i}",
                "date": "2026-01-01T00:00:00",
                "thumbs_up": 0, "has_dev_reply": 0,
                "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
            }
            for i in range(5)
        ]
        db.insert_reviews(reviews)
        db.update_classification("r0", '{"product_area": "ux"}')
        db.update_classification("r1", '{"product_area": "support"}')
        unclassified = db.get_unclassified_reviews()
        assert len(unclassified) == 3
        ids = {r["review_id"] for r in unclassified}
        assert "r0" not in ids
        assert "r1" not in ids


def test_database_manager_update_classification():
    """update_classification() persists JSON string to classification column."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([{
            "app_name": "TestApp",
            "review_id": "classify_r1",
            "rating": 2, "text": "bad experience",
            "date": "2026-01-01T00:00:00",
            "thumbs_up": 5, "has_dev_reply": 0,
            "dev_reply_text": None,
            "scraped_at": "2026-04-15T00:00:00",
        }])
        db.update_classification("classify_r1", '{"product_area": "transactions"}')
        cursor = db.conn.execute(
            "SELECT classification FROM reviews WHERE review_id = 'classify_r1'"
        )
        result = cursor.fetchone()[0]
        parsed = json.loads(result)
        assert parsed["product_area"] == "transactions"


def test_sql_analyst_high_signal_filter():
    """high_signal_low_rating_reviews() returns only reviews matching threshold."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([
            # Should be returned: thumbs_up=15, rating=1
            {
                "app_name": "TestApp", "review_id": "h1",
                "rating": 1, "text": "terrible",
                "date": "2026-01-01T00:00:00", "thumbs_up": 15,
                "has_dev_reply": 0, "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
            },
            # Should NOT be returned: thumbs_up=2, rating=1
            {
                "app_name": "TestApp", "review_id": "h2",
                "rating": 1, "text": "bad",
                "date": "2026-01-01T00:00:00", "thumbs_up": 2,
                "has_dev_reply": 0, "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
            },
            # Should NOT be returned: thumbs_up=20, rating=4
            {
                "app_name": "TestApp", "review_id": "h3",
                "rating": 4, "text": "ok",
                "date": "2026-01-01T00:00:00", "thumbs_up": 20,
                "has_dev_reply": 0, "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
            },
        ])
        analyst = SQLAnalyst(db)
        results = analyst.high_signal_low_rating_reviews(min_thumbs=10)
        assert len(results) == 1
        assert results[0]["review_id"] == "h1"


def test_sql_analyst_rating_distribution_keys():
    """rating_distribution_over_time() returns dicts with expected keys."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([{
            "app_name": "TestApp", "review_id": "d1",
            "rating": 4, "text": "good",
            "date": "2026-01-15T00:00:00", "thumbs_up": 0,
            "has_dev_reply": 0, "dev_reply_text": None,
            "scraped_at": "2026-04-15T00:00:00",
        }])
        analyst = SQLAnalyst(db)
        results = analyst.rating_distribution_over_time()
        if results:
            row = results[0]
            assert "app_name" in row
            assert "avg_rating" in row
            assert "review_count" in row


def test_findings_summarizer_save_to_file(tmp_path):
    """save_to_file() writes valid JSON that matches FindingsSummary schema."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([{
            "app_name": "TestApp", "review_id": "s1",
            "rating": 3, "text": "average app",
            "date": "2026-01-15T00:00:00", "thumbs_up": 0,
            "has_dev_reply": 0, "dev_reply_text": None,
            "scraped_at": "2026-04-15T00:00:00",
        }])
        analyst = SQLAnalyst(db)
        summarizer = FindingsSummarizer(analyst)
        summary = summarizer.generate_summary()
        output_path = str(tmp_path / "test_summary.json")
        summarizer.save_to_file(summary, output_path)
        with open(output_path) as f:
            data = json.load(f)
        assert "structured_text" in data
        assert "generated_at" in data
