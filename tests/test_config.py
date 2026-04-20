import pytest

from src.config import Config


def test_config_raises_on_missing_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config.from_env() should raise ValueError listing all missing keys."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ValueError, match="Missing required environment variables"):
        Config.from_env()


def test_config_from_env_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config.from_env() returns correct values when all keys are set."""
    monkeypatch.setenv("GEMINI_API_KEY", "test_gemini_key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test_openrouter_key")
    config = Config.from_env()
    assert config.gemini_api_key == "test_gemini_key"
    assert config.openrouter_api_key == "test_openrouter_key"
