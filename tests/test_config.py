from datetime import date

import pytest
from pydantic import SecretStr

from gym_ops.config import Settings


def test_defaults_without_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    settings = Settings(_env_file=None)

    assert settings.MODEL_ID == "anthropic/claude-haiku-4.5"
    assert settings.DB_PATH == "data/gym.db"
    assert settings.SEED == 42
    assert settings.SEED_MEMBERS == 300
    assert date(2026, 9, 25) == settings.REFERENCE_DATE
    assert settings.INPUT_USD_PER_MTOK == 1.00
    assert settings.OUTPUT_USD_PER_MTOK == 5.00
    assert settings.MATCH_WINDOW_DAYS == 5
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        settings.require_openrouter_api_key()


def test_api_key_is_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_key = "sk-or-v1-test-not-a-real-key"
    monkeypatch.setenv("OPENROUTER_API_KEY", fake_key)
    settings = Settings(_env_file=None)

    assert isinstance(settings.OPENROUTER_API_KEY, SecretStr)
    assert fake_key not in repr(settings)
    assert fake_key not in str(settings)
    assert settings.require_openrouter_api_key().get_secret_value() == fake_key
