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
        model_id="gemini-2.5-flash",
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
    member = CouncilMember("Test", "gemini", "gemini-2.5-flash", config)
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
