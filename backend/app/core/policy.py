"""Section 4.4: the decision policy. The only place an invoice can become AUTO_APPROVE.

Rules, in order of precedence:
1. REJECT for an exact duplicate, or for a GSTIN that is present but not in a valid format.
2. AUTO_APPROVE only if every check was run and passed, the grand total is within the limit,
   and the lowest extraction confidence meets the minimum.
3. Everything else is NEEDS_REVIEW, with a reason for each thing that blocked approval.

The policy fails closed: a missing check, a missing total or an unknown confidence all
block approval. Nothing here reads a database, a prompt or any model output as an
instruction. It only looks at check results and two numbers.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.core.matching.models import CheckResult
from app.schemas.enums import CheckName, Decision

# Reason codes that are not check names.
OVER_LIMIT = "over_auto_approve_limit"
LOW_CONFIDENCE = "low_confidence"
CONFIDENCE_UNKNOWN = "confidence_unknown"
TOTAL_UNKNOWN = "grand_total_unknown"
CHECK_NOT_RUN = "check_not_run"
# Codes the pipeline passes in via ``forced_review`` (see build plan 7.2).
EXTRACTION_FAILED = "extraction_failed"
AGENT_STEP_LIMIT = "agent_step_limit"
LLM_UNAVAILABLE = "llm_unavailable"

_REJECTING_CHECKS = {CheckName.EXACT_DUPLICATE, CheckName.GSTIN_FORMAT}

# A check the engine skips when one of these failed first (so the skip is already explained).
_PREREQUISITES: dict[CheckName, set[CheckName]] = {
    CheckName.PO_VENDOR_MATCH: {CheckName.PO_EXISTS},
    CheckName.LINE_COVERAGE: {CheckName.PO_EXISTS, CheckName.PO_VENDOR_MATCH},
    CheckName.UNIT_PRICE: {CheckName.PO_EXISTS, CheckName.PO_VENDOR_MATCH},
    CheckName.QUANTITY: {CheckName.PO_EXISTS, CheckName.PO_VENDOR_MATCH},
}


@dataclass(frozen=True)
class PolicyConfig:
    auto_approve_limit: Decimal = Decimal("50000")
    min_confidence: float = 0.9


@dataclass(frozen=True)
class Reason:
    code: str
    message: str


@dataclass(frozen=True)
class PolicyDecision:
    decision: Decision
    reasons: tuple[Reason, ...]


def decide(
    results: Sequence[CheckResult],
    grand_total: Decimal | None,
    min_extraction_confidence: float | None,
    config: PolicyConfig,
    forced_review: Iterable[Reason] = (),
) -> PolicyDecision:
    """Combine check results into a decision.

    ``forced_review`` lets the pipeline demand human review for reasons outside the checks
    (extraction failed, agent step limit, LLM unavailable). It can only make the outcome
    stricter: it never turns a REJECT into anything else and never causes an approval.
    """
    reasons: list[Reason] = []
    reject = False

    failed = [r for r in results if not r.passed]
    for r in failed:
        reasons.append(Reason(str(r.name), r.message))
        # REJECT needs a CONCLUSIVE finding. "Could not check" (inconclusive) is not "found a
        # duplicate" or "found a malformed GSTIN"; it goes to a human instead.
        if r.name in _REJECTING_CHECKS and not r.inconclusive:
            reject = True

    present = {r.name for r in results}
    failed_names = {r.name for r in failed}
    # Checks that depend on the PO are skipped by the engine when the PO is missing or belongs
    # to someone else. That is already explained by the failed prerequisite, so it is not
    # listed twice. Either way approval stays blocked, because ``reasons`` is then non-empty.
    for name in CheckName:
        if name in present:
            continue
        prerequisites = _PREREQUISITES.get(name)
        if prerequisites is not None and (
            failed_names & prerequisites or CheckName.PO_EXISTS not in present
        ):
            continue
        reasons.append(Reason(f"{CHECK_NOT_RUN}:{name}", f"The check '{name}' was not run."))

    if grand_total is None:
        reasons.append(Reason(TOTAL_UNKNOWN, "The grand total is unknown."))
    elif grand_total > config.auto_approve_limit:
        reasons.append(
            Reason(
                OVER_LIMIT,
                f"Total {grand_total:.2f} exceeds the auto-approve limit "
                f"{config.auto_approve_limit:.2f}.",
            )
        )

    if min_extraction_confidence is None:
        reasons.append(Reason(CONFIDENCE_UNKNOWN, "Extraction confidence is unknown."))
    elif min_extraction_confidence < config.min_confidence:
        reasons.append(
            Reason(
                LOW_CONFIDENCE,
                f"Lowest field confidence {min_extraction_confidence:.2f} is below the "
                f"required {config.min_confidence:.2f}.",
            )
        )

    reasons.extend(forced_review)

    if reject:
        return PolicyDecision(Decision.REJECT, tuple(reasons))
    if reasons:
        return PolicyDecision(Decision.NEEDS_REVIEW, tuple(reasons))
    return PolicyDecision(Decision.AUTO_APPROVE, ())
