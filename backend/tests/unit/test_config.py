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
    assert s.llm_provider == "ollama"
    assert s.allow_remote_llm is False


def test_env_overrides_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_APPROVE_LIMIT", "1234.50")
    monkeypatch.setenv("OLLAMA_TEXT_MODEL", "some-other-model")
    s = Settings(_env_file=None)
    assert s.auto_approve_limit == Decimal("1234.50")
    assert s.ollama_text_model == "some-other-model"


def test_min_confidence_must_be_a_probability(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIN_CONFIDENCE", "1.5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_usd_to_inr_is_unset_by_default_and_a_blank_value_means_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert Settings(_env_file=None).usd_to_inr is None
    monkeypatch.setenv("USD_TO_INR", "")  # what docker-compose passes when it is not set
    assert Settings(_env_file=None).usd_to_inr is None
    monkeypatch.setenv("USD_TO_INR", "84.5")
    assert Settings(_env_file=None).usd_to_inr == Decimal("84.5")


@pytest.mark.parametrize("bad", ["0", "-3", "abc"])
def test_usd_to_inr_must_be_a_positive_number(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("USD_TO_INR", bad)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)
