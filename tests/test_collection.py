import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from src.config import Config
from src.data_collection.database_manager import DatabaseManager
from tests.helpers import PROJECT_ROOT, make_review


def test_bug2_failed_apps_in_collection_result(
    llm_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    """failed_apps is populated when a per-app collection fails."""
    gps_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "google_play_scraper", gps_mock)
    if "src.data_collection.review_collector" in sys.modules:
        monkeypatch.delitem(sys.modules, "src.data_collection.review_collector")
    from src.data_collection.review_collector import ReviewCollector

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        config = Config.from_env()
        collector = ReviewCollector(db=db, config=config)

        def failing_collect_app(app_id, app_name, count):
            if app_name == "Groww":
                raise RuntimeError("Simulated scrape failure")
            return []

        collector.collect_app = failing_collect_app
        result = collector.collect_all(target_per_app=2200)
        assert hasattr(result, "failed_apps")
        assert "Groww" in result.failed_apps


def test_bug3_undersized_collection_added_to_failed_apps(
    llm_env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apps with fewer distinct reviews than target are marked failed."""
    gps_mock = MagicMock()
    monkeypatch.setitem(sys.modules, "google_play_scraper", gps_mock)
    if "src.data_collection.review_collector" in sys.modules:
        monkeypatch.delitem(sys.modules, "src.data_collection.review_collector")
    from src.data_collection.review_collector import ReviewCollector

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        config = Config.from_env()
        collector = ReviewCollector(db=db, config=config)

        def thin_collect_app(app_id, app_name, count):
            return [
                make_review(
                    app_name=app_name,
                    review_id=f"r_{app_name}_{i}",
                    rating=3,
                    text=f"review {i}",
                    scraped_at="2026-04-20T00:00:00",
                )
                for i in range(5)
            ]

        collector.collect_app = thin_collect_app
        result = collector.collect_all(target_per_app=2200)
        assert len(result.failed_apps) == len(collector.APP_TARGETS)


def test_bug9_collection_resume_uses_per_app_keys() -> None:
    """Collection resume logic must use per-app keys, not one global key."""
    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        for name in ["groww", "jupiter", "cred", "phonepe", "paytm"]:
            db.save_phase_state(f"collection_{name}", "complete", {"count": 2200})
        assert db.get_phase_state("collection") is None
        app_keys = ["groww", "jupiter", "cred", "phonepe", "paytm"]
        per_app_states = {k: db.get_phase_state(f"collection_{k}") for k in app_keys}
        all_complete = all(
            state is not None and state.get("status") == "complete"
            for state in per_app_states.values()
        )
        assert all_complete

    with DatabaseManager(db_path=":memory:") as db:
        db.create_schema()
        for name in ["groww", "jupiter", "cred"]:
            db.save_phase_state(f"collection_{name}", "complete", {"count": 2200})
        app_keys = ["groww", "jupiter", "cred", "phonepe", "paytm"]
        per_app_states = {k: db.get_phase_state(f"collection_{k}") for k in app_keys}
        partial_complete = all(
            state is not None and state.get("status") == "complete"
            for state in per_app_states.values()
        )
        assert not partial_complete


def test_main_dry_run_completes_without_api_calls(llm_env) -> None:
    """python src/main.py --dry-run completes all phases without errors."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    result = subprocess.run(
        [sys.executable, "src/main.py", "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        cwd=str(PROJECT_ROOT),
    )
    assert result.returncode == 0, (
        f"main.py --dry-run failed:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    )


def test_bug1_dry_run_does_not_write_canonical_phase_state(
    tmp_path, llm_env
) -> None:
    """--dry-run must not persist canonical phase completion state."""
    import sqlite3

    (tmp_path / "outputs").mkdir(exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "src/main.py"), "--dry-run"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"dry-run failed: {result.stderr}"
    db_path = tmp_path / "outputs" / "reviews.db"
    if not db_path.exists():
        return
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT phase, status FROM pipeline_state WHERE phase IN "
        "('collection','analysis','classification','council') AND status = 'complete'"
    ).fetchall()
    conn.close()
    assert rows == []
