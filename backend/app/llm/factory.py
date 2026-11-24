"""Choose the LLM provider from settings."""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.llm.base import LLMProvider
from app.llm.ollama import OllamaProvider
from app.llm.replay import ReplayProvider


def build_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "replay":
        return ReplayProvider(Path(settings.replay_fixture_dir))
    return OllamaProvider.from_settings(settings)
