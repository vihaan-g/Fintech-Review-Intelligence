def test_project_structure():
    """Confirms the project skeleton was created correctly."""
    import os
    assert os.path.exists("src/config.py")
    assert os.path.exists("src/data_collection/database_manager.py")
    assert os.path.exists("src/council/council_orchestrator.py")
    assert os.path.exists(".claude/skills/prompt-optimizer/SKILL.md")
    assert os.path.exists(".claude/skills/multi-agent-patterns/SKILL.md")
    assert os.path.exists(".claude/agents/council-orchestrator.md")


import pytest
from src.config import Config
from src.data_collection.database_manager import DatabaseManager


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
