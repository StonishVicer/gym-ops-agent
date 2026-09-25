"""Application settings loaded from environment variables and `.env`."""

from datetime import date
from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Optional here so DB/MCP components run without it; the extractor calls
    # `require_openrouter_api_key()` to enforce it.
    OPENROUTER_API_KEY: SecretStr | None = None
    MODEL_ID: str = "anthropic/claude-haiku-4.5"
    DB_PATH: str = "data/gym.db"

    # Deterministic seed (docs/SPEC.md FR-1, NFR-6). Synthetic data is generated
    # relative to REFERENCE_DATE, never the wall clock, so re-runs are identical.
    SEED: int = 42
    SEED_MEMBERS: int = Field(default=300, ge=20)
    REFERENCE_DATE: date = date(2026, 9, 25)

    # Pricing per 1M tokens for anthropic/claude-haiku-4.5.
    # Source: https://openrouter.ai/anthropic/claude-haiku-4.5 (retrieved 2026-09-25).
    INPUT_USD_PER_MTOK: float = 1.00
    OUTPUT_USD_PER_MTOK: float = 5.00

    # Reconciliation: a reference-less transfer links to a bill only if its date is
    # within this many days of the bill's due date (docs/adr/0005-reconciliation-rules.md).
    MATCH_WINDOW_DAYS: int = Field(default=5, ge=0)

    def require_openrouter_api_key(self) -> SecretStr:
        if self.OPENROUTER_API_KEY is None:
            raise ValueError("OPENROUTER_API_KEY is not set; copy .env.example to .env.")
        return self.OPENROUTER_API_KEY


@lru_cache
def get_settings() -> Settings:
    return Settings()
