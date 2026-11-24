"""Application configuration.

Every tolerance, limit and credential comes from environment variables
(see ``.env.example``). Nothing business-relevant is hard-coded in the rules.
"""

from decimal import Decimal
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---
    database_url: str = "postgresql+psycopg://trimatch:trimatch@localhost:5432/trimatch"

    # --- LLM ---
    anthropic_api_key: str = ""
    llm_model: str = "claude-sonnet-5-5"
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2

    # --- Agent ---
    agent_max_tool_calls: int = 8

    # --- Policy (see build plan section 4.4) ---
    auto_approve_limit: Decimal = Decimal("50000")
    min_confidence: float = Field(default=0.9, ge=0.0, le=1.0)

    # --- Matching tolerances (see build plan sections 4.1 and 4.2) ---
    price_tolerance_pct: Decimal = Decimal("1")
    arithmetic_tolerance: Decimal = Decimal("1")
    duplicate_window_days: int = 30

    # --- Uploads ---
    max_upload_mb: int = 10

    # --- Langfuse tracing ---
    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
