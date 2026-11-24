from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_defaults_match_build_plan() -> None:
    s = Settings(_env_file=None)
    assert s.auto_approve_limit == Decimal("50000")
    assert s.min_confidence == 0.9
    assert s.price_tolerance_pct == Decimal("1")
    assert s.agent_max_tool_calls == 8
    assert s.llm_model == "claude-sonnet-5-5"


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_APPROVE_LIMIT", "1234.50")
    monkeypatch.setenv("LLM_MODEL", "some-other-model")
    s = Settings(_env_file=None)
    assert s.auto_approve_limit == Decimal("1234.50")
    assert s.llm_model == "some-other-model"


def test_min_confidence_must_be_a_probability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIN_CONFIDENCE", "1.5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
