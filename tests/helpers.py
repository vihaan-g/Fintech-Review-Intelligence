from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def make_review(
    app_name: str = "TestApp",
    review_id: str = "r1",
    rating: int = 4,
    text: str = "Great app",
    date: str = "2026-01-01T00:00:00",
    thumbs_up: int = 0,
    has_dev_reply: int = 0,
    dev_reply_text: str | None = None,
    scraped_at: str = "2026-04-15T00:00:00",
    classification: str | None = None,
) -> dict:
    return {
        "app_name": app_name,
        "review_id": review_id,
        "rating": rating,
        "text": text,
        "date": date,
        "thumbs_up": thumbs_up,
        "has_dev_reply": has_dev_reply,
        "dev_reply_text": dev_reply_text,
        "scraped_at": scraped_at,
        "classification": classification,
    }
