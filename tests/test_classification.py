import json

import pytest

from src.classification.batch_processor import BatchProcessor, BatchResult
from src.classification.review_classifier import (
    ClassificationResult,
    OpenRouterAuthError,
    OpenRouterRateLimitError,
    ReviewClassifier,
)
from src.config import Config
from src.data_collection.database_manager import DatabaseManager
from tests.helpers import make_review


def test_review_classifier_parse_failure_never_raises(llm_env) -> None:
    """_parse_batch_response returns parse_failed results on bad JSON."""
    classifier = ReviewClassifier(Config.from_env())
    results = classifier._parse_batch_response("not valid json", batch_size=3)
    assert len(results) == 3
    assert all(result.parse_failed for result in results)
    assert all(result.confidence == 0.0 for result in results)


def test_review_classifier_strips_markdown_fences(llm_env) -> None:
    """_parse_batch_response handles JSON wrapped in markdown fences."""
    classifier = ReviewClassifier(Config.from_env())
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


def test_review_classifier_rejects_invalid_product_area(llm_env) -> None:
    """_parse_batch_response returns parse_failed when product_area is invalid."""
    classifier = ReviewClassifier(Config.from_env())
    bad_item = {
        "product_area": "not_a_valid_area",
        "specific_feature_request": None,
        "workflow_breakdown": False,
        "confidence": 0.8,
    }
    results = classifier._parse_batch_response(json.dumps([bad_item]), batch_size=1)
    assert results[0].parse_failed


def test_batch_processor_skips_if_complete(llm_env) -> None:
    """BatchProcessor.run() returns immediately when phase is already complete."""
    config = Config.from_env()
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.save_phase_state("classification", "complete")
        processor = BatchProcessor(ReviewClassifier(config), db)
        result = processor.run()
        assert result.total_classified == 0
        assert result.batches_processed == 0


def test_classification_round_trip_persists_fields(llm_env) -> None:
    """Classification JSON round-trips through the database intact."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id="rt1", rating=2, text="kyc stuck", thumbs_up=7)])
        result = ClassificationResult(
            product_area="onboarding",
            specific_feature_request="retry KYC button",
            workflow_breakdown=True,
            confidence=0.87,
            raw_response="{}",
            parse_failed=False,
        )
        payload = json.dumps(
            {
                "product_area": result.product_area,
                "specific_feature_request": result.specific_feature_request,
                "workflow_breakdown": result.workflow_breakdown,
                "confidence": result.confidence,
                "parse_failed": result.parse_failed,
            }
        )
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


def test_parse_failed_result_uses_unclassified_sentinel(llm_env) -> None:
    """_make_parse_failed_result returns 'unclassified', not 'ux'."""
    result = ReviewClassifier(Config.from_env())._make_parse_failed_result()
    assert result.product_area == "unclassified"
    assert result.parse_failed is True


def test_parse_batch_response_bracket_slice_with_preamble(llm_env) -> None:
    """_parse_batch_response extracts JSON array even with preamble text."""
    classifier = ReviewClassifier(Config.from_env())
    valid_item = {
        "product_area": "transactions",
        "specific_feature_request": None,
        "workflow_breakdown": False,
        "confidence": 0.85,
    }
    raw = f"Here is the JSON array you requested:\n{json.dumps([valid_item])}\nDone."
    results = classifier._parse_batch_response(raw, batch_size=1)
    assert len(results) == 1
    assert not results[0].parse_failed
    assert results[0].product_area == "transactions"


def test_openrouter_auth_error_propagates_from_classify_batch(llm_env, monkeypatch) -> None:
    """OpenRouterAuthError is not swallowed by classify_batch."""
    classifier = ReviewClassifier(Config.from_env())

    def raise_auth(*args, **kwargs):
        raise OpenRouterAuthError("HTTP 401")

    monkeypatch.setattr(classifier, "_call_openrouter", raise_auth)
    with pytest.raises(OpenRouterAuthError):
        classifier.classify_batch([{"text": "test review"}])


def test_batch_processor_iteration_cap(llm_env) -> None:
    """BatchProcessor.run() terminates when iteration cap is reached."""
    config = Config.from_env()
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id=f"cap{i}", rating=2, text=f"review {i}") for i in range(5)])

        def mock_classify(reviews):
            return [
                ClassificationResult(
                    product_area="unclassified",
                    specific_feature_request=None,
                    workflow_breakdown=False,
                    confidence=0.0,
                    raw_response="",
                    parse_failed=True,
                )
                for _ in reviews
            ]

        classifier = ReviewClassifier(config)
        classifier.classify_batch = mock_classify
        processor = BatchProcessor(classifier=classifier, db=db)
        processor.SLEEP_BETWEEN_BATCHES = 0.0
        result = processor.run()
        assert result.batches_processed <= (5 // processor.BATCH_SIZE) + 6


def test_batch_result_quota_exhausted_does_not_mark_complete() -> None:
    """BatchResult with status=quota_exhausted should never mark phase complete."""
    result = BatchResult(total_classified=130, parse_failures=0, status="quota_exhausted")
    assert result.status != "complete"


def test_batch_result_auth_error_status() -> None:
    """BatchResult with status=auth_error carries the correct status and message."""
    result = BatchResult(
        total_classified=0,
        parse_failures=0,
        status="auth_error",
        message="401 Unauthorized",
    )
    assert result.status == "auth_error"
    assert result.message == "401 Unauthorized"


def test_batch_processor_resume_count_reflects_checkpoint(
    llm_env, caplog: pytest.LogCaptureFixture
) -> None:
    """Resume logging should report already-classified and remaining counts."""
    config = Config.from_env()
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id=f"rc{i}", rating=3, text=f"r{i}") for i in range(25)])
        for i in range(10):
            db.update_classification(f"rc{i}", '{"product_area": "ux"}')
        db.save_phase_state("classification", "in_progress", {"total_classified": 10})

        classifier = ReviewClassifier(config)
        classifier.classify_batch = lambda reviews: [
            ClassificationResult(
                product_area="ux",
                specific_feature_request=None,
                workflow_breakdown=False,
                confidence=0.9,
                raw_response="",
                parse_failed=False,
            )
            for _ in reviews
        ]
        processor = BatchProcessor(classifier=classifier, db=db)
        processor.SLEEP_BETWEEN_BATCHES = 0.0

        with caplog.at_level("INFO", logger="BatchProcessor"):
            result = processor.run()
        assert result.status == "complete"
        assert result.total_classified == 25
        assert result.batches_processed == 2
        messages = [
            record.getMessage()
            for record in caplog.records
            if "Resuming classification" in record.getMessage()
        ]
        assert messages
        assert "10 already classified" in messages[0]
        assert "15 remaining" in messages[0]


def test_batch_result_total_classified_uses_cumulative_db_count(llm_env) -> None:
    """BatchProcessor should persist the cumulative classified count after resumed runs."""
    config = Config.from_env()
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id=f"cum{i}", rating=3, text=f"r{i}") for i in range(25)])
        for i in range(10):
            db.update_classification(f"cum{i}", '{"product_area": "ux"}')

        classifier = ReviewClassifier(config)
        classifier.classify_batch = lambda reviews: [
            ClassificationResult(
                product_area="ux",
                specific_feature_request=None,
                workflow_breakdown=False,
                confidence=0.9,
                raw_response="",
                parse_failed=False,
            )
            for _ in reviews
        ]
        processor = BatchProcessor(classifier=classifier, db=db)
        processor.SLEEP_BETWEEN_BATCHES = 0.0
        result = processor.run()

        assert result.total_classified == 25
        state = db.get_phase_state("classification")
        assert state is not None
        assert state["metadata"]["total_classified"] == 25


def test_batch_processor_no_unclassified_preserves_cumulative_total(llm_env) -> None:
    """A no-op rerun should keep cumulative totals in the classification checkpoint."""
    config = Config.from_env()
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id=f"done{i}", rating=3, text=f"r{i}") for i in range(12)])
        for i in range(12):
            db.update_classification(f"done{i}", '{"product_area": "ux"}')

        processor = BatchProcessor(classifier=ReviewClassifier(config), db=db)
        result = processor.run()

        assert result.status == "complete"
        assert result.total_classified == 12
        state = db.get_phase_state("classification")
        assert state is not None
        assert state["metadata"]["total_classified"] == 12
        assert state["metadata"]["status"] == "complete"


def test_batch_processor_no_unclassified_writes_debug_result_file(
    llm_env, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A no-op rerun should still refresh classification_complete.json."""
    monkeypatch.chdir(tmp_path)
    config = Config.from_env()
    with DatabaseManager(db_path=str(tmp_path / "test.db")) as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id=f"file{i}", rating=3, text=f"r{i}") for i in range(5)])
        for i in range(5):
            db.update_classification(f"file{i}", '{"product_area": "ux"}')

        processor = BatchProcessor(classifier=ReviewClassifier(config), db=db)
        result = processor.run()

    assert result.total_classified == 5
    output_path = tmp_path / "outputs" / "classification_complete.json"
    assert output_path.exists()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["total_classified"] == 5
    assert payload["status"] == "complete"


def test_classifier_fast_fails_on_first_attempt_rate_limit(
    llm_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_call_openrouter raises immediately on first-attempt 429 with no prior success."""
    classifier = ReviewClassifier(Config.from_env())
    call_count = [0]

    class FakeResponse:
        status_code = 429
        text = "Quota exceeded"
        headers: dict = {}

    def raise_429(*args, **kwargs):
        import httpx

        call_count[0] += 1
        raise httpx.HTTPStatusError("429", request=None, response=FakeResponse())  # type: ignore[arg-type]

    monkeypatch.setattr("httpx.post", raise_429)
    monkeypatch.setattr(
        "time.sleep",
        lambda seconds: (_ for _ in ()).throw(
            AssertionError(f"Unexpected sleep({seconds})")
        ),
    )

    with pytest.raises(OpenRouterRateLimitError, match="very first request"):
        classifier._call_openrouter("test prompt")
    assert call_count[0] == 1


def test_classifier_still_retries_429_after_success(
    llm_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After a prior success, a later 429 still retries normally."""
    classifier = ReviewClassifier(Config.from_env())
    classifier._has_succeeded = True
    call_count = [0]

    class FakeResponse:
        status_code = 429
        text = "Quota exceeded"
        headers: dict = {}

    def raise_429(*args, **kwargs):
        import httpx

        call_count[0] += 1
        raise httpx.HTTPStatusError("429", request=None, response=FakeResponse())  # type: ignore[arg-type]

    monkeypatch.setattr("httpx.post", raise_429)
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    with pytest.raises(OpenRouterRateLimitError):
        classifier._call_openrouter("test prompt")
    assert call_count[0] == 5


def test_bug4_iteration_cap_returns_incomplete_status(llm_env) -> None:
    """BatchProcessor returns status='incomplete' when reviews remain."""
    config = Config.from_env()
    classifier = ReviewClassifier(config)
    classifier.classify_batch = lambda reviews: [
        ClassificationResult(
            product_area="ux",
            specific_feature_request=None,
            workflow_breakdown=False,
            confidence=0.9,
            raw_response="",
            parse_failed=False,
        )
        for _ in reviews
    ]

    db = DatabaseManager(db_path=":memory:")
    db.__enter__()
    db.create_schema()
    db.insert_reviews([make_review(review_id=f"ic2_{i}", rating=2, text=f"review {i}", scraped_at="2026-04-20T00:00:00") for i in range(10)])
    db.get_unclassified_reviews = lambda limit=10: []
    db.get_unclassified_count = lambda: 10
    db.get_classified_count = lambda: 0

    processor = BatchProcessor(classifier=classifier, db=db)
    processor.SLEEP_BETWEEN_BATCHES = 0.0
    result = processor.run()
    db.__exit__(None, None, None)
    assert result.status == "incomplete"
    assert result.remaining_unclassified == 10


def test_bug5_string_false_parsed_correctly(llm_env) -> None:
    """workflow_breakdown='false' must parse to False, not True."""
    classifier = ReviewClassifier(Config.from_env())
    item = {
        "product_area": "ux",
        "specific_feature_request": None,
        "workflow_breakdown": "false",
        "confidence": 0.8,
    }
    results = classifier._parse_batch_response(json.dumps([item]), batch_size=1)
    assert results[0].workflow_breakdown is False
    assert not results[0].parse_failed

    item_true = {**item, "workflow_breakdown": "true"}
    results_true = classifier._parse_batch_response(json.dumps([item_true]), batch_size=1)
    assert results_true[0].workflow_breakdown is True
