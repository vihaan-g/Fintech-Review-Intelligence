import json

from src.analysis.findings_summarizer import FindingsSummarizer
from src.analysis.sql_analyst import SQLAnalyst
from src.data_collection.database_manager import DatabaseManager
from tests.helpers import make_review


def test_sql_analyst_methods_return_correct_types() -> None:
    """SQLAnalyst methods return expected container types."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        reviews = [
            make_review(
                review_id=f"r{i}",
                rating=i % 5 + 1,
                text=f"review text {i} upi cashback",
                date="2026-01-15T00:00:00",
                thumbs_up=i * 2,
                has_dev_reply=i % 2,
                dev_reply_text="Thanks" if i % 2 else None,
            )
            for i in range(10)
        ]
        db.insert_reviews(reviews)
        analyst = SQLAnalyst(db)
        assert isinstance(analyst.cross_app_summary(), dict)
        assert isinstance(analyst.keyword_frequency(["upi", "cashback"]), dict)
        assert isinstance(analyst.high_signal_low_rating_reviews(min_thumbs=0), list)
        assert isinstance(analyst.rating_distribution_over_time(), list)


def test_findings_summarizer_generates_structured_text() -> None:
    """generate_summary() returns a summary with non-empty structured_text."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        reviews = [
            make_review(
                review_id=f"r{i}",
                rating=(i % 5) + 1,
                text=f"test review {i}",
                date="2026-01-15T00:00:00",
                thumbs_up=i,
            )
            for i in range(20)
        ]
        db.insert_reviews(reviews)
        summary = FindingsSummarizer(SQLAnalyst(db)).generate_summary()
        assert isinstance(summary.structured_text, str)
        assert len(summary.structured_text) > 100
        assert "TestApp" in summary.structured_text


def test_developer_reply_impact_handles_no_replies() -> None:
    """developer_reply_impact() returns 0.0 for apps with no replies."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([
            make_review(review_id=f"r{i}", rating=1, text="bad app") for i in range(5)
        ])
        result = SQLAnalyst(db).developer_reply_impact()
        assert "TestApp" in result
        assert result["TestApp"]["avg_rating_with_reply"] == 0.0
        assert result["TestApp"]["reply_rate_pct"] == 0.0


def test_keyword_frequency_returns_empty_dict_on_no_matches() -> None:
    """keyword_frequency() returns empty dict when no reviews match."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(text="great app")])
        result = SQLAnalyst(db).keyword_frequency(["zzznomatch"])
        assert result == {}


def test_sql_analyst_high_signal_filter() -> None:
    """high_signal_low_rating_reviews() respects thumbs-up and rating filters."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews(
            [
                make_review(review_id="h1", rating=1, text="terrible", thumbs_up=15),
                make_review(review_id="h2", rating=1, text="bad", thumbs_up=2),
                make_review(review_id="h3", rating=4, text="ok", thumbs_up=20),
            ]
        )
        results = SQLAnalyst(db).high_signal_low_rating_reviews(min_thumbs=10)
        assert len(results) == 1
        assert results[0]["review_id"] == "h1"


def test_sql_analyst_rating_distribution_keys() -> None:
    """rating_distribution_over_time() returns rows with expected keys."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id="d1", date="2026-01-15T00:00:00")])
        results = SQLAnalyst(db).rating_distribution_over_time()
        if results:
            row = results[0]
            assert "app_name" in row
            assert "avg_rating" in row
            assert "review_count" in row


def test_findings_summarizer_save_to_file(tmp_path) -> None:
    """save_to_file() writes valid JSON matching summary fields."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id="s1", rating=3, text="average app", date="2026-01-15T00:00:00")])
        summarizer = FindingsSummarizer(SQLAnalyst(db))
        summary = summarizer.generate_summary()
        output_path = tmp_path / "test_summary.json"
        summarizer.save_to_file(summary, str(output_path))
        with open(output_path, encoding="utf-8") as file_handle:
            data = json.load(file_handle)
        assert "structured_text" in data
        assert "generated_at" in data


def test_classification_breakdown_empty_on_no_data(tmp_path) -> None:
    """classification_breakdown() returns {} when no classified reviews exist."""
    with DatabaseManager(db_path=str(tmp_path / "test.db")) as db:
        db.create_schema()
        result = SQLAnalyst(db=db).classification_breakdown()
        assert result == {}


def test_enrich_skips_if_no_classified_reviews(tmp_path, monkeypatch) -> None:
    """enrich_with_classification() returns False when no classified reviews exist."""
    monkeypatch.chdir(tmp_path)
    summary = {
        "structured_text": "## Data Overview\n- test",
        "cross_app_stats": {},
        "high_signal_reviews": [],
        "generated_at": "2026-04-18T00:00:00",
    }
    (tmp_path / "outputs").mkdir(exist_ok=True)
    with open("outputs/findings_summary.json", "w", encoding="utf-8") as file_handle:
        json.dump(summary, file_handle)

    with DatabaseManager(db_path=str(tmp_path / "test.db")) as db:
        db.create_schema()
        enriched = FindingsSummarizer(analyst=SQLAnalyst(db=db)).enrich_with_classification()

    assert enriched is False
    with open("outputs/findings_summary.json", encoding="utf-8") as file_handle:
        result = json.load(file_handle)
    assert "classification_breakdown" not in result


def test_review_volume_by_week_groups_by_iso_week() -> None:
    """review_volume_by_week() returns YYYY-WW strings including week 00."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews(
            [
                make_review(review_id="w1", rating=3, text="early january review", date="2026-01-01T00:00:00"),
                make_review(review_id="w2", rating=4, text="midyear review", date="2026-07-15T00:00:00"),
            ]
        )
        rows = SQLAnalyst(db).review_volume_by_week()
        assert rows
        weeks = {row["week"] for row in rows}
        for week in weeks:
            assert len(week) == 7 and week[4] == "-"
        assert any(week.endswith("-00") for week in weeks)


def test_bug10_most_common_rating_deterministic_on_tie() -> None:
    """most_common_rating must be deterministic on ties; lowest rating wins."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        reviews = []
        for i in range(5):
            reviews.append(make_review(app_name="TieApp", review_id=f"tie_1_{i}", rating=1, text="bad", scraped_at="2026-04-20T00:00:00"))
            reviews.append(make_review(app_name="TieApp", review_id=f"tie_5_{i}", rating=5, text="great", scraped_at="2026-04-20T00:00:00"))
        db.insert_reviews(reviews)
        result = SQLAnalyst(db).cross_app_summary()
        assert result["TieApp"]["most_common_rating"] == 1
