"""Currency display helpers for the API."""

from __future__ import annotations

from decimal import Decimal


def cost_in_inr(avg_cost_usd: float, usd_to_inr: Decimal | None) -> float | None:
    """The average model cost in rupees, or ``None`` if it cannot be stated honestly.

    - A cost of zero is zero rupees in any currency, so no exchange rate is needed (this is the
      normal case with a local model).
    - A real cost needs the configured rate. Without one the answer is ``None`` (unknown); a
      rate is never guessed.
    """
    if avg_cost_usd == 0:
        return 0.0
    if usd_to_inr is None:
        return None
    return avg_cost_usd * float(usd_to_inr)
