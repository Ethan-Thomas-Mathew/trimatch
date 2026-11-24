"""Application configuration.

Every tolerance, limit and credential comes from environment variables
(see ``.env.example``). Nothing business-relevant is hard-coded in the rules.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Database ---
    # 127.0.0.1, not "localhost": Windows tries IPv6 (::1) first, and the container port is
    # published on IPv4 loopback only, so "localhost" costs 5 s per connection.
    database_url: str = "postgresql+psycopg://trimatch:trimatch@127.0.0.1:5432/trimatch"

    # --- LLM (local only: no API keys exist anywhere in this project; see D-011) ---
    llm_provider: Literal["ollama", "replay"] = "ollama"
    ollama_host: str = "http://127.0.0.1:11434"
    allow_remote_llm: bool = False  # invoice data stays on this machine unless you opt in
    ollama_text_model: str = "qwen2.5:7b"
    ollama_vision_model: str = "qwen2.5vl:7b"
    llm_timeout_seconds: float = 120.0  # local models are slower than hosted ones
    llm_max_retries: int = 2
    replay_fixture_dir: str = "../evals/fixtures/extraction"

    # --- Extraction ---
    min_text_chars: int = 80  # fewer extracted characters than this => treat as a scan
    max_vision_pages: int = 3

    # --- Agent ---
    agent_max_tool_calls: int = 8

    # --- Policy (see build plan section 4.4) ---
    auto_approve_limit: Decimal = Decimal("50000")
    min_confidence: float = Field(default=0.9, ge=0.0, le=1.0)

    # --- Matching tolerances (see build plan sections 4.1 and 4.2) ---
    price_tolerance_pct: Decimal = Decimal("1")
    arithmetic_tolerance: Decimal = Decimal("1")
    duplicate_window_days: int = 30

    # --- Buyer (the company receiving the invoices; Karnataka in the synthetic dataset) ---
    buyer_state_code: str = "29"

    # --- Uploads ---
    max_upload_mb: int = 10
    upload_dir: str = "../data/uploads"

    # --- API ---
    reviewer_username: str = "reviewer"
    # Empty means "generate a random password at startup and log it once" (secure by default).
    reviewer_password: str = ""
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # --- Langfuse tracing ---
    langfuse_host: str = "http://127.0.0.1:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    @model_validator(mode="after")
    def _llm_host_must_be_local(self) -> Settings:
        host = urlparse(self.ollama_host).hostname
        if host not in {"127.0.0.1", "localhost", "::1"} and not self.allow_remote_llm:
            raise ValueError(
                f"OLLAMA_HOST points at {host!r}, which is not this machine. Invoice contents "
                "would leave it. Set ALLOW_REMOTE_LLM=true only if you really intend that."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
