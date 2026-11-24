"""Build the core engine's config objects from application settings.

Kept outside ``app.core`` on purpose: ``core`` has no dependency on settings, the database or
the environment, so it stays pure and trivially testable.
"""

from app.config import Settings
from app.core.matching import MatchConfig
from app.core.policy import PolicyConfig


def match_config(settings: Settings) -> MatchConfig:
    return MatchConfig(
        price_tolerance_pct=settings.price_tolerance_pct,
        arithmetic_tolerance=settings.arithmetic_tolerance,
        duplicate_window_days=settings.duplicate_window_days,
    )


def policy_config(settings: Settings) -> PolicyConfig:
    return PolicyConfig(
        auto_approve_limit=settings.auto_approve_limit,
        min_confidence=settings.min_confidence,
    )
