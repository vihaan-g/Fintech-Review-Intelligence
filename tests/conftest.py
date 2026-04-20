import os

import pytest


os.makedirs("outputs", exist_ok=True)


@pytest.fixture
def llm_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test_key")
