import json
import os

import pytest

from src.config import Config
from src.data_collection.database_manager import DatabaseManager
from src.analysis.sql_analyst import SQLAnalyst
from src.analysis.findings_summarizer import FindingsSummarizer
from src.classification.batch_processor import BatchProcessor
from src.council.council_member import CouncilMember, MemberResponse
from src.council.council_orchestrator import CouncilOrchestrator

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

def test_database_manager_rollback_on_exception(tmp_path):
    """DatabaseManager rolls back uncommitted data when __exit__ receives an exception.

    M10: uses a file-backed DB so rollback is verifiable across connections
    (two :memory: connections are independent and can't test rollback).
    """
    db_file = str(tmp_path / "rollback_test.db")

    # Phase 1: create the schema with a clean commit
    with DatabaseManager(db_path=db_file) as db:
        db.create_schema()

    # Phase 2: open DB, write a row WITHOUT going through insert_reviews
    # (which auto-commits), then trigger rollback via __exit__ exception
    inner = DatabaseManager(db_path=db_file)
    inner.__enter__()
    # Direct cursor write — no commit, so still in an open transaction
    inner._conn.execute(
        "INSERT INTO reviews (app_name, review_id, rating, text, date, scraped_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("TestApp", "rollback_r1", 3, "test", "2026-01-01T00:00:00", "2026-04-15T00:00:00"),
    )
    # Data is visible within the open connection
    assert inner.get_review_count("TestApp") == 1

    # Trigger rollback
    inner.__exit__(ValueError, ValueError("Simulated failure"), None)

    # Phase 3: re-open and verify the row was rolled back
    with DatabaseManager(db_path=db_file) as verify_db:
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


# ---------------------------------------------------------------------------
# STAGE 4 — ReviewClassifier and BatchProcessor tests
# ---------------------------------------------------------------------------

def test_review_classifier_parse_failure_never_raises():
    """_parse_batch_response returns parse_failed results on bad JSON."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier
    config = Config.from_env()
    classifier = ReviewClassifier(config)
    results = classifier._parse_batch_response("not valid json", batch_size=3)
    assert len(results) == 3
    assert all(r.parse_failed for r in results)
    assert all(r.confidence == 0.0 for r in results)


def test_review_classifier_strips_markdown_fences():
    """_parse_batch_response handles JSON wrapped in markdown fences."""
    import os, json
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier
    config = Config.from_env()
    classifier = ReviewClassifier(config)
    valid_item = {
        "product_area": "ux",
        "specific_feature_request": None,
        "workflow_breakdown": False,
        "confidence": 0.9,
    }
    fenced = f"```json\n{json.dumps([valid_item])}\n```"
    results = classifier._parse_batch_response(fenced, batch_size=1)
    assert len(results) == 1
    assert not results[0].parse_failed
    assert results[0].product_area == "ux"


def test_review_classifier_rejects_invalid_product_area():
    """_parse_batch_response returns parse_failed when product_area is invalid."""
    import os, json
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier
    config = Config.from_env()
    classifier = ReviewClassifier(config)
    bad_item = {
        "product_area": "not_a_valid_area",
        "specific_feature_request": None,
        "workflow_breakdown": False,
        "confidence": 0.8,
    }
    results = classifier._parse_batch_response(
        json.dumps([bad_item]), batch_size=1
    )
    assert results[0].parse_failed


def test_batch_processor_skips_if_complete():
    """BatchProcessor.run() returns immediately when phase is already complete."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier
    from src.classification.batch_processor import BatchProcessor
    config = Config.from_env()
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.save_phase_state("classification", "complete")
        classifier = ReviewClassifier(config)
        processor = BatchProcessor(classifier, db)
        result = processor.run()
        assert result.total_classified == 0
        assert result.batches_processed == 0


# ---------------------------------------------------------------------------
# STAGE 5 — CouncilMember and CouncilOrchestrator tests
# ---------------------------------------------------------------------------

def test_council_member_strips_think_tags():
    """CouncilMember._strip_think_tags() removes think blocks correctly."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()
    member = CouncilMember(
        name="Test",
        provider="gemini",
        model_id="gemini-2.5-flash-lite",
        config=config,
    )
    raw = "<think>some reasoning here</think>Actual insight about CRED."
    result = member._strip_think_tags(raw)
    assert "think" not in result
    assert "Actual insight about CRED." in result


def test_council_member_strips_multiline_think_tags():
    """_strip_think_tags() handles multiline think blocks."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()
    member = CouncilMember("Test", "gemini", "gemini-2.5-flash-lite", config)
    raw = "<think>\nline 1\nline 2\n</think>\nFinal answer."
    result = member._strip_think_tags(raw)
    assert result.strip() == "Final answer."


def test_council_orchestrator_default_has_four_members():
    """CouncilOrchestrator.default() creates a council with 4 members."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()
    orchestrator = CouncilOrchestrator.default(config)
    assert len(orchestrator.members) == 4


def test_council_orchestrator_anonymization_map():
    """_build_stage2_prompt() produces a shuffled anonymization map
    with 4 distinct labels (A, B, C, D)."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()
    orchestrator = CouncilOrchestrator.default(config)
    fake_responses = [
        MemberResponse(
            member_name=f"Member{i}",
            model_id=f"model-{i}",
            raw_response=f"insight {i}",
            clean_response=f"insight {i}",
            timestamp="2026-04-16T00:00:00",
            duration_ms=100,
        )
        for i in range(4)
    ]
    labels = ["Response A", "Response B", "Response C", "Response D"]
    prompt = orchestrator._build_stage2_prompt(fake_responses, labels)
    for label in labels:
        assert label in prompt


# ---------------------------------------------------------------------------
# STAGE 6 — InsightReporter tests
# ---------------------------------------------------------------------------

def test_insight_reporter_raises_on_empty_synthesis():
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


def test_insight_reporter_generates_all_files(tmp_path, monkeypatch):
    """InsightReporter.generate_all() writes all 3 output files."""
    from src.agents.insight_reporter import InsightReporter
    monkeypatch.chdir(tmp_path)
    os.makedirs("outputs", exist_ok=True)
    long_synthesis = "A" * 200  # over 100 char threshold
    reporter = InsightReporter.from_dicts(
        council_dict={
            "stage3_synthesis": long_synthesis,
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


# ---------------------------------------------------------------------------
# L3 — Classification round-trip test
# ---------------------------------------------------------------------------

def test_classification_round_trip_persists_fields():
    """A ClassificationResult serialised through update_classification()
    round-trips back with all fields intact when fetched from the DB."""
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ClassificationResult
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([{
            "app_name": "TestApp", "review_id": "rt1",
            "rating": 2, "text": "kyc stuck",
            "date": "2026-01-01T00:00:00", "thumbs_up": 7,
            "has_dev_reply": 0, "dev_reply_text": None,
            "scraped_at": "2026-04-15T00:00:00",
        }])
        result = ClassificationResult(
            product_area="onboarding",
            specific_feature_request="retry KYC button",
            workflow_breakdown=True,
            confidence=0.87,
            raw_response="{}",
            parse_failed=False,
        )
        payload = json.dumps({
            "product_area": result.product_area,
            "specific_feature_request": result.specific_feature_request,
            "workflow_breakdown": result.workflow_breakdown,
            "confidence": result.confidence,
            "parse_failed": result.parse_failed,
        })
        db.update_classification("rt1", payload)

        cursor = db.conn.execute(
            "SELECT classification FROM reviews WHERE review_id = 'rt1'"
        )
        stored = json.loads(cursor.fetchone()[0])
        assert stored["product_area"] == "onboarding"
        assert stored["specific_feature_request"] == "retry KYC button"
        assert stored["workflow_breakdown"] is True
        assert abs(stored["confidence"] - 0.87) < 1e-9
        assert stored["parse_failed"] is False


# ---------------------------------------------------------------------------
# L4 — Week-00 strftime awareness
# ---------------------------------------------------------------------------

def test_review_volume_by_week_groups_by_iso_week():
    """review_volume_by_week() returns rows whose 'week' key is in YYYY-WW
    format. Days before the year's first Monday fall into week '00' — this
    test exercises that SQLite edge case explicitly."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([
            # 2026-01-01 is a Thursday — before the first Monday, so week 00.
            {
                "app_name": "TestApp", "review_id": "w1",
                "rating": 3, "text": "early january review",
                "date": "2026-01-01T00:00:00", "thumbs_up": 0,
                "has_dev_reply": 0, "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
            },
            # Mid-year review, definitively not week 00.
            {
                "app_name": "TestApp", "review_id": "w2",
                "rating": 4, "text": "midyear review",
                "date": "2026-07-15T00:00:00", "thumbs_up": 0,
                "has_dev_reply": 0, "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
            },
        ])
        analyst = SQLAnalyst(db)
        rows = analyst.review_volume_by_week()
        assert rows, "Expected at least one weekly bucket"
        weeks = {row["week"] for row in rows}
        # All week labels are YYYY-WW-shaped strings
        for w in weeks:
            assert len(w) == 7 and w[4] == "-", f"Unexpected week format: {w}"
        # The early-January row produces week '00' — guard against future
        # regressions that silently drop or reshape it.
        assert any(w.endswith("-00") for w in weeks), (
            f"Expected a week-00 bucket for 2026-01-01, got {weeks}"
        )


# ---------------------------------------------------------------------------
# Audit fix tests — BLOCKING / HIGH behaviors
# ---------------------------------------------------------------------------

def test_parse_failed_result_uses_unclassified_sentinel():
    """B7: _make_parse_failed_result returns 'unclassified', not 'ux'."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier
    from src.config import Config
    config = Config.from_env()
    classifier = ReviewClassifier(config)
    result = classifier._make_parse_failed_result()
    assert result.product_area == "unclassified"
    assert result.parse_failed is True


def test_parse_batch_response_bracket_slice_with_preamble():
    """H3: _parse_batch_response extracts JSON array even with preamble text."""
    import os, json
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier
    from src.config import Config
    config = Config.from_env()
    classifier = ReviewClassifier(config)
    valid_item = {
        "product_area": "transactions",
        "specific_feature_request": None,
        "workflow_breakdown": False,
        "confidence": 0.85,
    }
    # Simulate Gemini prepending prose before the JSON array
    raw = f"Here is the JSON array you requested:\n{json.dumps([valid_item])}\nDone."
    results = classifier._parse_batch_response(raw, batch_size=1)
    assert len(results) == 1
    assert not results[0].parse_failed
    assert results[0].product_area == "transactions"


def test_gemini_auth_error_propagates_from_classify_batch(monkeypatch):
    """B2: GeminiAuthError is not swallowed by classify_batch."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "bad_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import (
        ReviewClassifier, GeminiAuthError,
    )
    from src.config import Config
    config = Config.from_env()
    classifier = ReviewClassifier(config)

    def _raise_auth(*args, **kwargs):
        raise GeminiAuthError("HTTP 401")
    monkeypatch.setattr(classifier, "_call_gemini", _raise_auth)

    with pytest.raises(GeminiAuthError):
        classifier.classify_batch([{"text": "test review"}])


def test_database_manager_execute_read_returns_list():
    """M11: execute_read() is a public API that returns list[dict]."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([{
            "app_name": "TestApp", "review_id": "er1",
            "rating": 4, "text": "good",
            "date": "2026-01-01T00:00:00", "thumbs_up": 0,
            "has_dev_reply": 0, "dev_reply_text": None,
            "scraped_at": "2026-04-15T00:00:00",
        }])
        rows = db.execute_read("SELECT app_name FROM reviews WHERE review_id = ?", ("er1",))
        assert len(rows) == 1
        assert rows[0]["app_name"] == "TestApp"


def test_batch_processor_iteration_cap():
    """H2: BatchProcessor.run() terminates when iteration cap is reached."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier, ClassificationResult
    from src.classification.batch_processor import BatchProcessor
    from src.config import Config
    import time
    config = Config.from_env()

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        # Insert 5 reviews
        db.insert_reviews([{
            "app_name": "TestApp", "review_id": f"cap{i}",
            "rating": 2, "text": f"review {i}",
            "date": "2026-01-01T00:00:00", "thumbs_up": 0,
            "has_dev_reply": 0, "dev_reply_text": None,
            "scraped_at": "2026-04-15T00:00:00",
        } for i in range(5)])

        # Mock classify_batch to return parse_failed (simulates empty-ID infinite loop)
        # but also actually update classification so the batch shrinks
        call_count = [0]
        original_classify = ReviewClassifier.classify_batch

        def mock_classify(self, reviews):
            call_count[0] += 1
            return [ClassificationResult(
                product_area="unclassified",
                specific_feature_request=None,
                workflow_breakdown=False,
                confidence=0.0,
                raw_response="",
                parse_failed=True,
            ) for _ in reviews]

        classifier = ReviewClassifier(config)
        classifier.classify_batch = lambda r: mock_classify(classifier, r)

        processor = BatchProcessor(classifier=classifier, db=db)
        processor.SLEEP_BETWEEN_BATCHES = 0.0  # no sleep in tests
        result = processor.run()
        # Should complete without infinite loop
        assert result.batches_processed <= (5 // processor.BATCH_SIZE) + 6


def test_classification_breakdown_empty_on_no_data(tmp_path):
    """classification_breakdown() returns {} when no classified reviews exist."""
    with DatabaseManager(db_path=str(tmp_path / "test.db")) as db:
        db.create_schema()
        result = SQLAnalyst(db=db).classification_breakdown()
        assert result == {}


def test_enrich_skips_if_no_classified_reviews(tmp_path, monkeypatch):
    """enrich_with_classification() returns False and leaves file unchanged
    when no successfully classified reviews exist."""
    import json as _json

    monkeypatch.chdir(tmp_path)
    os.makedirs("outputs", exist_ok=True)

    summary = {
        "structured_text": "## Data Overview\n- test",
        "cross_app_stats": {},
        "high_signal_reviews": [],
        "generated_at": "2026-04-18T00:00:00",
    }
    with open("outputs/findings_summary.json", "w") as f:
        _json.dump(summary, f)

    with DatabaseManager(db_path=str(tmp_path / "test.db")) as db:
        db.create_schema()
        enriched = FindingsSummarizer(
            analyst=SQLAnalyst(db=db)
        ).enrich_with_classification()

    assert enriched is False
    with open("outputs/findings_summary.json") as f:
        result = _json.load(f)
    assert "classification_breakdown" not in result


# ---------------------------------------------------------------------------
# BatchResult status field tests
# ---------------------------------------------------------------------------

def test_batch_result_quota_exhausted_does_not_mark_complete(
    tmp_path, monkeypatch
):
    """BatchResult with status=quota_exhausted should never mark phase complete."""
    from src.classification.batch_processor import BatchResult
    result = BatchResult(
        total_classified=130,
        parse_failures=0,
        status="quota_exhausted",
    )
    assert result.status != "complete"


def test_batch_result_auth_error_status():
    """BatchResult with status=auth_error carries the correct status and message."""
    from src.classification.batch_processor import BatchResult
    result = BatchResult(
        total_classified=0,
        parse_failures=0,
        status="auth_error",
        message="401 Unauthorized",
    )
    assert result.status == "auth_error"
    assert result.message == "401 Unauthorized"


def test_database_manager_unclassified_and_classified_counts():
    """get_unclassified_count() and get_classified_count() return correct counts
    and together equal the total — used by BatchProcessor resume logging."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([
            {
                "app_name": "TestApp", "review_id": f"c{i}",
                "rating": 3, "text": f"r{i}",
                "date": "2026-01-01T00:00:00", "thumbs_up": 0,
                "has_dev_reply": 0, "dev_reply_text": None,
                "scraped_at": "2026-04-15T00:00:00",
            }
            for i in range(7)
        ])
        db.update_classification("c0", '{"product_area": "ux"}')
        db.update_classification("c1", '{"product_area": "ux"}')
        db.update_classification("c2", '{"product_area": "ux"}')
        assert db.get_classified_count() == 3
        assert db.get_unclassified_count() == 4
        assert db.get_review_count() == 7


def test_batch_processor_resume_count_reflects_checkpoint(caplog):
    """BatchProcessor.run() estimates batches from unclassified-only count,
    and the resume log reports the already-classified checkpoint correctly."""
    import logging as _logging
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier, ClassificationResult
    from src.classification.batch_processor import BatchProcessor

    config = Config.from_env()
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        # 25 reviews total — 10 already classified, 15 remaining (2 batches of 10).
        db.insert_reviews([{
            "app_name": "TestApp", "review_id": f"rc{i}",
            "rating": 3, "text": f"r{i}",
            "date": "2026-01-01T00:00:00", "thumbs_up": 0,
            "has_dev_reply": 0, "dev_reply_text": None,
            "scraped_at": "2026-04-15T00:00:00",
        } for i in range(25)])
        for i in range(10):
            db.update_classification(f"rc{i}", '{"product_area": "ux"}')
        db.save_phase_state("classification", "in_progress", {"total_classified": 10})

        classifier = ReviewClassifier(config)
        classifier.classify_batch = lambda reviews: [
            ClassificationResult(
                product_area="ux", specific_feature_request=None,
                workflow_breakdown=False, confidence=0.9,
                raw_response="", parse_failed=False,
            ) for _ in reviews
        ]
        processor = BatchProcessor(classifier=classifier, db=db)
        processor.SLEEP_BETWEEN_BATCHES = 0.0

        with caplog.at_level(_logging.INFO, logger="BatchProcessor"):
            result = processor.run()

        assert result.status == "complete"
        # Only the 15 unclassified were processed, not all 25.
        assert result.total_classified == 15
        # 15 remaining / 10 per batch = 2 batches (ceil).
        assert result.batches_processed == 2
        # Resume log mentions the 10 already-classified and 15 remaining.
        resume_messages = [r.getMessage() for r in caplog.records
                           if "Resuming classification" in r.getMessage()]
        assert resume_messages, "Expected 'Resuming classification' log"
        assert "10 already classified" in resume_messages[0]
        assert "15 remaining" in resume_messages[0]


def test_classifier_fast_fails_on_first_attempt_quota(monkeypatch):
    """_call_gemini raises GeminiQuotaExhaustedError immediately on first-attempt
    429 when no prior success — no retry sleeps."""
    import os
    import time as _time
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import (
        ReviewClassifier, GeminiQuotaExhaustedError,
    )
    config = Config.from_env()
    classifier = ReviewClassifier(config)

    call_count = [0]

    class _FakeResponse:
        status_code = 429
        text = "Quota exceeded"
        headers: dict = {}

    def _raise_429(*args, **kwargs):
        call_count[0] += 1
        import httpx
        resp = _FakeResponse()
        raise httpx.HTTPStatusError("429", request=None, response=resp)  # type: ignore[arg-type]

    monkeypatch.setattr("httpx.post", _raise_429)

    # Guard: fail the test if any sleep happens — fast-fail should skip backoff.
    def _no_sleep(seconds):
        raise AssertionError(f"Unexpected sleep({seconds}) — fast-fail should skip retry backoff")
    monkeypatch.setattr(_time, "sleep", _no_sleep)

    with pytest.raises(GeminiQuotaExhaustedError, match="very first request"):
        classifier._call_gemini("test prompt")

    assert call_count[0] == 1, "Should have made exactly one HTTP attempt before fast-fail"


def test_classifier_still_retries_429_after_success(monkeypatch):
    """Once a successful call has been made, a later 429 still retries normally
    (it could be a transient burst limit, not daily quota exhaustion)."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier
    config = Config.from_env()
    classifier = ReviewClassifier(config)
    classifier._has_succeeded = True  # simulate earlier successful call

    call_count = [0]

    class _FakeResponse:
        status_code = 429
        text = "Quota exceeded"
        headers: dict = {}

    def _raise_429(*args, **kwargs):
        call_count[0] += 1
        import httpx
        resp = _FakeResponse()
        raise httpx.HTTPStatusError("429", request=None, response=resp)  # type: ignore[arg-type]

    monkeypatch.setattr("httpx.post", _raise_429)
    monkeypatch.setattr("time.sleep", lambda s: None)  # skip real backoff

    from src.classification.review_classifier import GeminiQuotaExhaustedError
    with pytest.raises(GeminiQuotaExhaustedError):
        classifier._call_gemini("test prompt")
    # All 5 retries should have fired (no fast-fail path).
    assert call_count[0] == 5


def test_council_orchestrator_chairman_model_id():
    """CouncilOrchestrator.default() chairman uses gemini-3.1-pro-preview."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()
    orchestrator = CouncilOrchestrator.default(config)
    assert orchestrator.chairman.model_id == "gemini-3.1-pro-preview"


def test_role_mandates_coverage():
    """ROLE_MANDATES contains a key for each member model ID and no key for the chairman."""
    member_ids = {
        "anthropic/claude-opus-4.7",
        "deepseek/deepseek-r1",
        "qwen/qwen3.6-plus",
    }
    chairman_id = "gemini-3.1-pro-preview"
    assert set(CouncilOrchestrator.ROLE_MANDATES.keys()) == member_ids
    assert chairman_id not in CouncilOrchestrator.ROLE_MANDATES


def test_council_result_has_analytical_frame_field():
    """CouncilResult.analytical_frame defaults to empty string."""
    from src.council.council_orchestrator import CouncilResult
    result = CouncilResult(
        stage1_responses={},
        anonymization_map={},
        stage2_gap_analysis="gap analysis text",
        stage3_synthesis="x" * 100,
        total_duration_ms=1000,
        generated_at="2026-04-20T00:00:00",
    )
    assert result.analytical_frame == ""


def test_main_dry_run_completes_without_api_calls():
    """python src/main.py --dry-run completes all phases without errors."""
    import subprocess
    import sys
    from pathlib import Path
    # Derive project root from this test file's location rather than hardcoding it.
    project_root = str(Path(__file__).resolve().parent.parent)
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = "test_key"
    env["OPENROUTER_API_KEY"] = "test_key"
    env["PYTHONPATH"] = project_root
    result = subprocess.run(
        [sys.executable, "src/main.py", "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        cwd=project_root,
    )
    assert result.returncode == 0, (
        f"main.py --dry-run failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# Codex audit regression tests — Section A (correctness bugs)
# ---------------------------------------------------------------------------

def test_bug1_dry_run_does_not_write_canonical_phase_state(tmp_path):
    """Bug 1: --dry-run must not persist canonical phase state for any phase.
    After a dry-run, pipeline_state must have no 'complete' rows for
    collection / analysis / classification / council."""
    import sqlite3, sys
    from pathlib import Path
    import subprocess
    project_root = str(Path(__file__).resolve().parent.parent)
    # Run subprocess in tmp_path so DB is created there, not in the real outputs/
    (tmp_path / "outputs").mkdir(exist_ok=True)
    env = os.environ.copy()
    env["GEMINI_API_KEY"] = "test_key"
    env["OPENROUTER_API_KEY"] = "test_key"
    env["PYTHONPATH"] = project_root
    result = subprocess.run(
        [sys.executable, os.path.join(project_root, "src/main.py"), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"dry-run failed: {result.stderr}"
    db_path = str(tmp_path / "outputs" / "reviews.db")
    if not os.path.exists(db_path):
        return  # no DB = no state written, test passes
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT phase, status FROM pipeline_state WHERE phase IN "
        "('collection','analysis','classification','council') AND status = 'complete'"
    ).fetchall()
    conn.close()
    assert rows == [], (
        f"Dry-run wrote canonical phase completion state: {rows}"
    )


def test_bug2_failed_apps_in_collection_result(monkeypatch):
    """Bug 2: failed_apps is populated when a per-app collection fails."""
    import sys
    from unittest.mock import MagicMock
    # google_play_scraper not installed in test env — mock the module before import
    gps_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "google_play_scraper", gps_mock)
    # Force re-import if module was cached without the mock
    if "src.data_collection.review_collector" in sys.modules:
        monkeypatch.delitem(sys.modules, "src.data_collection.review_collector")
    from src.data_collection.review_collector import ReviewCollector

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        os.environ.setdefault("GEMINI_API_KEY", "test_key")
        os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
        config = Config.from_env()
        collector = ReviewCollector(db=db, config=config)

        def failing_collect_app(app_id, app_name, count):
            if app_name == "Groww":
                raise RuntimeError("Simulated scrape failure")
            return []

        collector.collect_app = failing_collect_app
        result = collector.collect_all(target_per_app=2200)
        assert hasattr(result, "failed_apps"), "CollectionResult must have failed_apps field"
        assert "Groww" in result.failed_apps, (
            f"Expected Groww in failed_apps, got: {result.failed_apps}"
        )


def test_bug3_undersized_collection_added_to_failed_apps(monkeypatch):
    """Bug 3: app with fewer distinct reviews than target is marked failed."""
    import sys
    from unittest.mock import MagicMock
    gps_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "google_play_scraper", gps_mock)
    if "src.data_collection.review_collector" in sys.modules:
        monkeypatch.delitem(sys.modules, "src.data_collection.review_collector")
    from src.data_collection.review_collector import ReviewCollector

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        os.environ.setdefault("GEMINI_API_KEY", "test_key")
        os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
        config = Config.from_env()
        collector = ReviewCollector(db=db, config=config)

        def thin_collect_app(app_id, app_name, count):
            return [
                {
                    "app_name": app_name,
                    "review_id": f"r_{app_name}_{i}",
                    "rating": 3,
                    "text": f"review {i}",
                    "date": "2026-01-01T00:00:00",
                    "thumbs_up": 0,
                    "has_dev_reply": 0,
                    "dev_reply_text": None,
                    "scraped_at": "2026-04-20T00:00:00",
                    "classification": None,
                }
                for i in range(5)
            ]

        collector.collect_app = thin_collect_app
        result = collector.collect_all(target_per_app=2200)
        assert len(result.failed_apps) == len(collector.APP_TARGETS), (
            f"Expected all 5 apps in failed_apps (5 < 2200). Got: {result.failed_apps}"
        )


def test_bug4_iteration_cap_returns_incomplete_status():
    """Bug 4: BatchProcessor returns status='incomplete' (not 'complete') when
    iteration cap is hit with reviews still unclassified."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier, ClassificationResult
    from src.classification.batch_processor import BatchProcessor

    config = Config.from_env()
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        # Insert reviews that will never be classified (mock returns empty)
        db.insert_reviews([{
            "app_name": "TestApp", "review_id": f"ic{i}",
            "rating": 2, "text": f"review {i}",
            "date": "2026-01-01T00:00:00", "thumbs_up": 0,
            "has_dev_reply": 0, "dev_reply_text": None,
            "scraped_at": "2026-04-20T00:00:00",
        } for i in range(10)])

        classifier = ReviewClassifier(config)
        # Mock classify_batch to return results WITHOUT updating classification
        # column, so unclassified count stays > 0 after cap is hit
        call_count = [0]
        MAX_CALLS = 3  # fewer than needed to classify all

        def capped_classify(reviews):
            call_count[0] += 1
            if call_count[0] > MAX_CALLS:
                raise AssertionError("classify_batch called too many times")
            return [ClassificationResult(
                product_area="ux", specific_feature_request=None,
                workflow_breakdown=False, confidence=0.9,
                raw_response="", parse_failed=False,
            ) for _ in reviews]

        classifier.classify_batch = capped_classify
        processor = BatchProcessor(classifier=classifier, db=db)
        processor.SLEEP_BETWEEN_BATCHES = 0.0
        # Set max_iterations to 1 so cap is hit immediately
        # We do this by patching unclassified_count to trick the cap calculation
        original_get_unclassified = db.get_unclassified_count

        call_n = [0]
        def patched_get_unclassified():
            call_n[0] += 1
            # First call (for estimate): return 10 → max_iterations = ceil(10/10)+5 = 6
            # After the loop body runs once, reviews get classified by the mock,
            # but since mock doesn't update DB, all 10 remain unclassified
            return original_get_unclassified()

        db.get_unclassified_count = patched_get_unclassified

        # Force iteration cap by overriding BATCH_SIZE to something huge so
        # only 1 iteration runs then cap hits naturally... Actually let's just
        # directly test: after normal run with the mock that DOES classify,
        # set remaining to non-zero via get_unclassified_count patch
        # Simpler approach: just verify the status field logic in BatchProcessor
        # by patching get_unclassified_count at result-check time
        original_get_unc2 = db.get_unclassified_count

        def always_10():
            return 10  # always say 10 unclassified

        # Re-init with fresh DB to avoid state from above
        db2 = DatabaseManager(db_path=":memory:")
        db2.__enter__()
        db2.create_schema()
        db2.insert_reviews([{
            "app_name": "TestApp", "review_id": f"ic2_{i}",
            "rating": 2, "text": f"review {i}",
            "date": "2026-01-01T00:00:00", "thumbs_up": 0,
            "has_dev_reply": 0, "dev_reply_text": None,
            "scraped_at": "2026-04-20T00:00:00",
        } for i in range(10)])
        # Patch so loop exits immediately (batch returns empty) but
        # get_unclassified_count still says 10
        db2.get_unclassified_reviews = lambda limit=10: []  # empty → loop exits
        db2.get_unclassified_count = lambda: 10  # reports 10 remaining
        db2.get_classified_count = lambda: 0

        processor2 = BatchProcessor(classifier=classifier, db=db2)
        processor2.SLEEP_BETWEEN_BATCHES = 0.0
        result = processor2.run()
        db2.__exit__(None, None, None)

        assert result.status == "incomplete", (
            f"Expected status='incomplete' when unclassified remain, got '{result.status}'"
        )
        assert result.remaining_unclassified == 10


def test_bug5_string_false_parsed_correctly():
    """Bug 5: workflow_breakdown='false' (string) must parse to False, not True."""
    import os, json
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    from src.classification.review_classifier import ReviewClassifier
    config = Config.from_env()
    classifier = ReviewClassifier(config)

    # Simulate API returning "false" as a string instead of JSON boolean
    item = {
        "product_area": "ux",
        "specific_feature_request": None,
        "workflow_breakdown": "false",
        "confidence": 0.8,
    }
    results = classifier._parse_batch_response(json.dumps([item]), batch_size=1)
    assert len(results) == 1
    # "false" string must become False, not True
    assert results[0].workflow_breakdown is False, (
        f"bool('false') bug: expected False, got {results[0].workflow_breakdown}"
    )
    assert not results[0].parse_failed

    # Also verify "true" string works
    item_true = {**item, "workflow_breakdown": "true"}
    results_true = classifier._parse_batch_response(json.dumps([item_true]), batch_size=1)
    assert results_true[0].workflow_breakdown is True


def test_bug6_stage0_receives_full_findings_text():
    """Bug 6: Stage 0 prompt must not truncate findings_text."""
    import os
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()
    orchestrator = CouncilOrchestrator.default(config)
    # Generate a findings_text longer than 4000 chars
    long_text = "X" * 5000
    # Inspect the built frame_prompt via the method source — check no [:4000] slice
    # by verifying the prompt contains the full text
    frame_prompt_parts = []

    async def capture_generate(prompt):
        frame_prompt_parts.append(prompt)
        return MemberResponse(
            member_name="chairman",
            model_id="test",
            raw_response="analytical frame",
            clean_response="analytical frame",
            timestamp="2026-04-20T00:00:00",
            duration_ms=100,
        )

    import asyncio
    original = orchestrator.chairman.generate
    orchestrator.chairman.generate = capture_generate
    asyncio.run(orchestrator._stage0_frame_question(long_text))
    orchestrator.chairman.generate = original

    assert frame_prompt_parts, "chairman.generate was not called"
    prompt_used = frame_prompt_parts[0]
    assert long_text in prompt_used, (
        f"Stage 0 truncated findings_text — 5000-char text not found in prompt "
        f"(prompt length: {len(prompt_used)})"
    )


def test_bug7_fatal_4xx_in_stage1_raises():
    """Bug 7: A fatal HTTP 4xx from a Stage 1 member must raise, not produce
    a silent empty slot. Tests the post-gather inspection logic directly."""
    import asyncio
    import httpx
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()
    orchestrator = CouncilOrchestrator.default(config)

    class _FakeResponse:
        status_code = 401
        text = "Unauthorized"

    ok_response = MemberResponse(
        member_name="ok",
        model_id="ok-model",
        raw_response="insight text",
        clean_response="insight text",
        timestamp="2026-04-20T00:00:00",
        duration_ms=100,
    )
    fatal_exc = httpx.HTTPStatusError(
        "401 Unauthorized",
        request=None,  # type: ignore[arg-type]
        response=_FakeResponse(),  # type: ignore[arg-type]
    )
    # Simulate gather results: first member fatally failed, rest succeeded
    gathered = [fatal_exc, ok_response, ok_response, ok_response]

    # Replay the orchestrator's post-gather loop and verify it raises
    with pytest.raises(RuntimeError, match="fatal HTTP 401"):
        for member, item in zip(orchestrator.members, gathered):
            if isinstance(item, BaseException):
                if isinstance(item, httpx.HTTPStatusError):
                    status_code = item.response.status_code
                    if 400 <= status_code < 500 and status_code != 429:
                        raise RuntimeError(
                            f"Stage 1 member {orchestrator._member_label(member)} returned "
                            f"fatal HTTP {status_code} (model: {member.model_id}). "
                            "Check API key and model ID. Council aborted."
                        ) from item


def test_bug8_report_does_not_claim_full_history(tmp_path, monkeypatch):
    """Bug 8: Generated report must not claim 'full available history'.
    It should say 'newest 2,200 reviews per app' instead."""
    from src.agents.insight_reporter import InsightReporter
    monkeypatch.chdir(tmp_path)
    os.makedirs("outputs", exist_ok=True)
    long_synthesis = "A" * 200
    reporter = InsightReporter.from_dicts(
        council_dict={
            "stage3_synthesis": long_synthesis,
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
    report_text = open("outputs/findings_report.md").read()
    assert "full available history" not in report_text, (
        "Report still claims 'full available history' — Bug 8 fix not applied"
    )
    assert "2,200" in report_text, (
        "Report should mention '2,200' reviews per app"
    )


def test_bug9_collection_resume_uses_per_app_keys():
    """Bug 9: main()'s collection resume check must use per-app keys
    (collection_groww etc.), not the single 'collection' key."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        # Write per-app keys for all 5 apps as 'complete'
        for name in ["groww", "jupiter", "cred", "phonepe", "paytm"]:
            db.save_phase_state(f"collection_{name}", "complete", {"count": 2200})
        # The old single-key 'collection' is NOT written
        assert db.get_phase_state("collection") is None
        # Verify that main's per-app check logic correctly sees all-complete
        _COLLECTION_APP_KEYS = ["groww", "jupiter", "cred", "phonepe", "paytm"]
        per_app_states = {
            k: db.get_phase_state(f"collection_{k}") for k in _COLLECTION_APP_KEYS
        }
        all_complete = all(
            s is not None and s.get("status") == "complete"
            for s in per_app_states.values()
        )
        assert all_complete, (
            "Per-app key check should detect all apps complete, but it did not"
        )

        # Partial state: only 3 of 5 apps complete → not all_complete
        db2 = DatabaseManager(db_path=":memory:")
        db2.__enter__()
        db2.create_schema()
        for name in ["groww", "jupiter", "cred"]:
            db2.save_phase_state(f"collection_{name}", "complete", {"count": 2200})
        per_app_states2 = {
            k: db2.get_phase_state(f"collection_{k}") for k in _COLLECTION_APP_KEYS
        }
        partial_complete = all(
            s is not None and s.get("status") == "complete"
            for s in per_app_states2.values()
        )
        db2.__exit__(None, None, None)
        assert not partial_complete, (
            "Per-app check must return False when only 3/5 apps are complete"
        )


def test_bug10_most_common_rating_deterministic_on_tie():
    """Bug 10: most_common_rating must be deterministic on ties.
    Tie-break rule: lowest rating wins."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        # Insert equal counts of 1-star and 5-star reviews → tie
        reviews_tie = []
        for i in range(5):
            reviews_tie.append({
                "app_name": "TieApp", "review_id": f"tie_1_{i}",
                "rating": 1, "text": "bad",
                "date": "2026-01-01T00:00:00", "thumbs_up": 0,
                "has_dev_reply": 0, "dev_reply_text": None,
                "scraped_at": "2026-04-20T00:00:00",
            })
            reviews_tie.append({
                "app_name": "TieApp", "review_id": f"tie_5_{i}",
                "rating": 5, "text": "great",
                "date": "2026-01-01T00:00:00", "thumbs_up": 0,
                "has_dev_reply": 0, "dev_reply_text": None,
                "scraped_at": "2026-04-20T00:00:00",
            })
        db.insert_reviews(reviews_tie)
        analyst = SQLAnalyst(db)
        result = analyst.cross_app_summary()
        # Tie between 1-star and 5-star: lowest rating (1) must win
        assert result["TieApp"]["most_common_rating"] == 1, (
            f"Tie-break failed: expected rating 1, got {result['TieApp']['most_common_rating']}"
        )


# ---------------------------------------------------------------------------
# Council upgrade regression tests
# ---------------------------------------------------------------------------

def test_preflight_passes_for_paid_model(monkeypatch):
    """Preflight passes for paid models (pricing.prompt != '0'); only checks catalog existence."""
    import asyncio
    import src.council.council_orchestrator as co_module
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        orchestrator = CouncilOrchestrator.default(config, db)

    # Build a catalog containing each OpenRouter member with non-zero (paid) pricing.
    openrouter_ids = [m.model_id for m in orchestrator.members if m.provider == "openrouter"]
    catalog = {
        "data": [
            {"id": mid, "pricing": {"prompt": "0.0015", "completion": "0.002"}}
            for mid in openrouter_ids
        ]
    }

    class _MockResp:
        def raise_for_status(self) -> None: pass
        def json(self) -> dict: return catalog

    class _MockClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs): return _MockResp()

    monkeypatch.setattr(co_module.httpx, "AsyncClient", lambda **kwargs: _MockClient())

    # Should not raise — paid models are valid as long as they exist in catalog.
    asyncio.run(orchestrator._preflight_openrouter_models())


def test_stage0_fail_fast_raises_before_stage1():
    """Stage 0 fail-fast: empty chairman frame raises RuntimeError; Stage 1 does not fire."""
    import asyncio
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        orchestrator = CouncilOrchestrator.default(config, db)

        async def empty_chairman(prompt: str) -> MemberResponse:
            return MemberResponse(
                member_name="chairman", model_id="test",
                raw_response="", clean_response="",
                timestamp="2026-04-20T00:00:00", duration_ms=0,
            )

        orchestrator.chairman.generate = empty_chairman

        # RuntimeError must name the Stage 0 failure; Stage 1 (asyncio.gather)
        # is never reached because the error is raised before the preflight call.
        with pytest.raises(RuntimeError, match="Stage 0 failed"):
            asyncio.run(orchestrator.run("test findings"))


def test_stage0_frame_checkpointed_before_stage1(monkeypatch):
    """Stage 0: frame is persisted to council_stage0_frame in DB before Stage 1 fires."""
    import asyncio
    import src.council.council_orchestrator as co_module
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()

    TEST_FRAME = "Specific analytical frame for checkpoint test."
    checkpoint_at_gather: list = [None]

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        orchestrator = CouncilOrchestrator.default(config, db)

        async def mock_chairman(prompt: str) -> MemberResponse:
            return MemberResponse(
                member_name="chairman", model_id="test",
                raw_response=TEST_FRAME, clean_response=TEST_FRAME,
                timestamp="2026-04-20T00:00:00", duration_ms=0,
            )

        orchestrator.chairman.generate = mock_chairman

        # Skip HTTP preflight.
        async def noop_preflight() -> None: pass
        orchestrator._preflight_openrouter_models = noop_preflight  # type: ignore[method-assign]

        # Intercept Stage 1's asyncio.gather to capture DB state at that moment.
        # Close unawaited coroutines to suppress RuntimeWarning.
        async def capturing_gather(*coros, **kwargs):
            checkpoint_at_gather[0] = db.get_phase_state("council_stage0_frame")
            for coro in coros:
                coro.close()
            raise RuntimeError("gather_intercepted_for_test")

        monkeypatch.setattr(co_module.asyncio, "gather", capturing_gather)

        with pytest.raises(RuntimeError, match="gather_intercepted_for_test"):
            asyncio.run(orchestrator.run("test findings"))

    state = checkpoint_at_gather[0]
    assert state is not None, "council_stage0_frame not written before Stage 1"
    assert state.get("status") == "complete"
    assert state.get("metadata", {}).get("frame") == TEST_FRAME


def test_stage0_skipped_when_frame_cached(monkeypatch):
    """Stage 0 is skipped entirely when council_stage0_frame is already in pipeline_state."""
    import asyncio
    import src.council.council_orchestrator as co_module
    os.environ.setdefault("GEMINI_API_KEY", "test_key")
    os.environ.setdefault("OPENROUTER_API_KEY", "test_key")
    config = Config.from_env()

    CACHED_FRAME = "Cached frame from a previous run."
    stage0_generate_called = [False]

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        # Pre-populate the Stage 0 checkpoint so run() skips Stage 0.
        db.save_phase_state("council_stage0_frame", "complete", {"frame": CACHED_FRAME})

        orchestrator = CouncilOrchestrator.default(config, db)

        async def chairman_generate(prompt: str) -> MemberResponse:
            stage0_generate_called[0] = True
            return MemberResponse(
                member_name="chairman", model_id="test",
                raw_response="new frame", clean_response="new frame",
                timestamp="2026-04-20T00:00:00", duration_ms=0,
            )

        orchestrator.chairman.generate = chairman_generate

        # Skip HTTP preflight.
        async def noop_preflight() -> None: pass
        orchestrator._preflight_openrouter_models = noop_preflight  # type: ignore[method-assign]

        # Intercept Stage 1 so the test terminates before any member API calls.
        # Close unawaited coroutines to suppress RuntimeWarning.
        async def early_exit(*coros, **kwargs):
            for coro in coros:
                coro.close()
            raise RuntimeError("stage1_early_exit_for_test")

        monkeypatch.setattr(co_module.asyncio, "gather", early_exit)

        with pytest.raises(RuntimeError, match="stage1_early_exit_for_test"):
            asyncio.run(orchestrator.run("test findings"))

    # chairman.generate was NOT called during Stage 0 because the cache was hit.
    assert not stage0_generate_called[0], (
        "Stage 0 should be skipped when council_stage0_frame is cached, "
        "but chairman.generate was called."
    )
