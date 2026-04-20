import json

from src.data_collection.database_manager import DatabaseManager
from tests.helpers import make_review


def test_database_manager_schema_and_insert() -> None:
    """DatabaseManager creates schema and inserts reviews correctly."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        inserted = db.insert_reviews([make_review(thumbs_up=5)])
        assert inserted == 1
        assert db.get_review_count("TestApp") == 1


def test_pipeline_state_checkpoint() -> None:
    """DatabaseManager saves and retrieves phase state correctly."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.save_phase_state("collection", "complete", {"count": 100})
        state = db.get_phase_state("collection")
        assert state["status"] == "complete"


def test_database_manager_rollback_on_exception(tmp_path) -> None:
    """DatabaseManager rolls back uncommitted data on context exit failure."""
    db_file = str(tmp_path / "rollback_test.db")

    with DatabaseManager(db_path=db_file) as db:
        db.create_schema()

    inner = DatabaseManager(db_path=db_file)
    inner.__enter__()
    inner._conn.execute(
        "INSERT INTO reviews (app_name, review_id, rating, text, date, scraped_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("TestApp", "rollback_r1", 3, "test", "2026-01-01T00:00:00", "2026-04-15T00:00:00"),
    )
    assert inner.get_review_count("TestApp") == 1
    inner.__exit__(ValueError, ValueError("Simulated failure"), None)

    with DatabaseManager(db_path=db_file) as verify_db:
        assert verify_db.get_review_count("TestApp") == 0


def test_database_manager_insert_deduplicates() -> None:
    """insert_reviews() with duplicate review_id inserts only once."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        review = make_review(review_id="dup_r1", thumbs_up=2)
        db.insert_reviews([review])
        db.insert_reviews([review])
        assert db.get_review_count("TestApp") == 1


def test_database_manager_both_tables_created() -> None:
    """create_schema() creates both reviews and pipeline_state tables."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        cursor = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "reviews" in tables
        assert "pipeline_state" in tables


def test_database_manager_get_review_count_per_app() -> None:
    """get_review_count() filters correctly by app_name."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        reviews = [
            make_review(app_name="AppA", review_id=f"a{i}", text="good") for i in range(3)
        ] + [
            make_review(app_name="AppB", review_id=f"b{i}", rating=3, text="ok")
            for i in range(2)
        ]
        db.insert_reviews(reviews)
        assert db.get_review_count("AppA") == 3
        assert db.get_review_count("AppB") == 2
        assert db.get_review_count() == 5


def test_database_manager_phase_state_upsert() -> None:
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
        assert cursor.fetchone()[0] == 1


def test_database_manager_get_unclassified_reviews() -> None:
    """get_unclassified_reviews() returns only reviews with classification IS NULL."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        reviews = [make_review(review_id=f"r{i}", rating=3, text=f"review {i}") for i in range(5)]
        db.insert_reviews(reviews)
        db.update_classification("r0", '{"product_area": "ux"}')
        db.update_classification("r1", '{"product_area": "support"}')
        unclassified = db.get_unclassified_reviews()
        assert len(unclassified) == 3
        ids = {r["review_id"] for r in unclassified}
        assert "r0" not in ids
        assert "r1" not in ids


def test_database_manager_update_classification() -> None:
    """update_classification() persists JSON string to classification column."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id="classify_r1", rating=2, text="bad experience", thumbs_up=5)])
        db.update_classification("classify_r1", '{"product_area": "transactions"}')
        cursor = db.conn.execute(
            "SELECT classification FROM reviews WHERE review_id = 'classify_r1'"
        )
        result = cursor.fetchone()[0]
        parsed = json.loads(result)
        assert parsed["product_area"] == "transactions"


def test_database_manager_execute_read_returns_list() -> None:
    """execute_read() is a public API that returns list[dict]."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id="er1", text="good")])
        rows = db.execute_read(
            "SELECT app_name FROM reviews WHERE review_id = ?",
            ("er1",),
        )
        assert len(rows) == 1
        assert rows[0]["app_name"] == "TestApp"


def test_database_manager_unclassified_and_classified_counts() -> None:
    """Classified and unclassified counts should add up to the total."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        db.insert_reviews([make_review(review_id=f"c{i}", rating=3, text=f"r{i}") for i in range(7)])
        db.update_classification("c0", '{"product_area": "ux"}')
        db.update_classification("c1", '{"product_area": "ux"}')
        db.update_classification("c2", '{"product_area": "ux"}')
        assert db.get_classified_count() == 3
        assert db.get_unclassified_count() == 4
        assert db.get_review_count() == 7
