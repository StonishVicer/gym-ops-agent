"""Application settings loaded from environment variables and `.env`."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Optional here so DB/MCP components run without it; the extractor calls
    # `require_openrouter_api_key()` to enforce it.
    OPENROUTER_API_KEY: SecretStr | None = None
    MODEL_ID: str = "anthropic/claude-haiku-4.5"
    DB_PATH: str = "data/gym.db"

    # Pricing per 1M tokens for anthropic/claude-haiku-4.5.
    # Source: https://openrouter.ai/anthropic/claude-haiku-4.5 (retrieved 2026-09-25).
    INPUT_USD_PER_MTOK: float = 1.00
    OUTPUT_USD_PER_MTOK: float = 5.00

    def require_openrouter_api_key(self) -> SecretStr:
        if self.OPENROUTER_API_KEY is None:
            raise ValueError("OPENROUTER_API_KEY is not set; copy .env.example to .env.")
        return self.OPENROUTER_API_KEY


@lru_cache
def get_settings() -> Settings:
    return Settings()
