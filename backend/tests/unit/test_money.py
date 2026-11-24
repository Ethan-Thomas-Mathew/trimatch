from decimal import Decimal

import pytest

from app.api.money import cost_in_inr


def test_zero_cost_is_zero_rupees_without_any_rate() -> None:
    assert cost_in_inr(0.0, None) == 0.0
    assert cost_in_inr(0.0, Decimal("85")) == 0.0


def test_a_real_cost_is_converted_with_the_configured_rate() -> None:
    assert cost_in_inr(0.02, Decimal("85")) == pytest.approx(1.7)
    assert cost_in_inr(1.0, Decimal("84.5")) == pytest.approx(84.5)


def test_a_real_cost_without_a_rate_is_unknown_never_guessed() -> None:
    assert cost_in_inr(0.02, None) is None
