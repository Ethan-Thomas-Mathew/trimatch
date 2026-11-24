"""The engine wiring and the decision policy (section 4.4)."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pytest

from app.core.matching import CheckResult, MatchConfig, PriorInvoice, run_three_way_match
from app.core.policy import (
    AGENT_STEP_LIMIT,
    CHECK_NOT_RUN,
    CONFIDENCE_UNKNOWN,
    LLM_UNAVAILABLE,
    LOW_CONFIDENCE,
    OVER_LIMIT,
    TOTAL_UNKNOWN,
    PolicyConfig,
    Reason,
    decide,
)
from app.schemas.enums import CheckName, Decision

from .factories import D, inputs, invoice, po, vendor

CFG = MatchConfig()
POLICY = PolicyConfig(auto_approve_limit=D("50000"), min_confidence=0.9)


def run(**overrides: object) -> list[CheckResult]:
    return run_three_way_match(inputs(**overrides), CFG)


def failed(results: list[CheckResult]) -> set[CheckName]:
    return {r.name for r in results if not r.passed}


def decision(results: list[CheckResult], total: object = D("2300.00"), conf: float | None = 1.0):  # type: ignore[no-untyped-def]
    return decide(results, total, conf, POLICY)  # type: ignore[arg-type]


# ----------------------------------------------------------------------- engine
def test_clean_invoice_passes_every_check_and_every_check_is_present() -> None:
    results = run()
    assert failed(results) == set()
    assert {r.name for r in results} == set(CheckName)  # all 11 ran


def test_missing_po_fails_po_exists_and_skips_dependent_checks() -> None:
    results = run(po=None)
    assert failed(results) == {CheckName.PO_EXISTS}
    names = {r.name for r in results}
    assert not names & {CheckName.UNIT_PRICE, CheckName.QUANTITY, CheckName.LINE_COVERAGE}


def test_po_of_another_vendor_is_not_used_for_matching() -> None:
    results = run(po=po(vendor_id=99))
    assert failed(results) == {CheckName.PO_VENDOR_MATCH}
    assert CheckName.UNIT_PRICE not in {r.name for r in results}


def test_unknown_vendor_fails_closed_everywhere_it_matters() -> None:
    results = run(vendor=None)
    bad = failed(results)
    assert {
        CheckName.GSTIN_MATCHES_VENDOR,
        CheckName.PO_VENDOR_MATCH,
        CheckName.EXACT_DUPLICATE,
        CheckName.POSSIBLE_DUPLICATE,
    } <= bad


def test_supplier_state_falls_back_to_the_gstin_prefix_for_tax_type() -> None:
    results = run(vendor=None)
    tax = next(r for r in results if r.name is CheckName.TAX_TYPE)
    assert tax.passed  # GSTIN starts with 29, same as the buyer, CGST+SGST charged


def test_price_and_quantity_problems_are_reported_independently() -> None:
    inv = invoice()
    bad = replace(inv.lines[0], unit_price=D("120.00"), line_total=D("1200.00"), qty=D("10"))
    inv2 = replace(
        inv,
        lines=(bad, inv.lines[1]),
        subtotal=D("2200.00"),
        cgst=D("168.00"),
        sgst=D("168.00"),
        tax_total=D("336.00"),
        grand_total=D("2536.00"),
    )
    results = run(invoice=inv2, received={1: D("8"), 2: D("5")})
    assert failed(results) == {CheckName.UNIT_PRICE, CheckName.QUANTITY}


def test_engine_detects_a_duplicate_through_prior_invoices() -> None:
    prior = PriorInvoice(1, "KS/24-25/0042", D("2300.00"), dt.date(2024, 7, 1))
    results = run(prior_invoices=(prior,))
    assert {CheckName.EXACT_DUPLICATE, CheckName.POSSIBLE_DUPLICATE} <= failed(results)


# ----------------------------------------------------------------------- policy
def test_clean_invoice_is_auto_approved() -> None:
    d = decision(run())
    assert d.decision is Decision.AUTO_APPROVE
    assert d.reasons == ()


def test_exact_duplicate_is_rejected() -> None:
    prior = PriorInvoice(1, "KS/24-25/0042", D("100"), dt.date(2023, 1, 1))
    d = decision(run(prior_invoices=(prior,)))
    assert d.decision is Decision.REJECT
    assert CheckName.EXACT_DUPLICATE in {r.code for r in d.reasons}


def test_invalid_gstin_format_is_rejected() -> None:
    d = decision(run(invoice=replace(invoice(), vendor_gstin="29AABCK1234F1Z")))
    assert d.decision is Decision.REJECT


def test_missing_gstin_goes_to_review_not_reject() -> None:
    d = decision(run(invoice=replace(invoice(), vendor_gstin=None)))
    assert d.decision is Decision.NEEDS_REVIEW


def test_valid_but_wrong_vendor_gstin_goes_to_review() -> None:
    d = decision(run(invoice=replace(invoice(), vendor_gstin="29AABCK9999F1ZR")))
    assert d.decision is Decision.NEEDS_REVIEW


def test_reject_wins_over_every_other_problem() -> None:
    prior = PriorInvoice(1, "KS/24-25/0042", D("2300.00"), dt.date(2024, 7, 1))
    d = decide(run(prior_invoices=(prior,)), D("999999"), 0.1, POLICY, [Reason("x", "y")])
    assert d.decision is Decision.REJECT
    codes = {r.code for r in d.reasons}
    assert {OVER_LIMIT, LOW_CONFIDENCE, "x"} <= codes  # still all explained


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        (D("49999.99"), Decision.AUTO_APPROVE),
        (D("50000.00"), Decision.AUTO_APPROVE),  # the limit itself is allowed
        (D("50000.01"), Decision.NEEDS_REVIEW),
    ],
)
def test_amount_limit_boundary(total: object, expected: Decision) -> None:
    assert decision(run(), total=total).decision is expected


def test_over_limit_reason_is_explained() -> None:
    d = decision(run(), total=D("60000"))
    assert [r.code for r in d.reasons] == [OVER_LIMIT]


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (1.0, Decision.AUTO_APPROVE),
        (0.9, Decision.AUTO_APPROVE),  # exactly the minimum is allowed
        (0.8999, Decision.NEEDS_REVIEW),
        (0.0, Decision.NEEDS_REVIEW),
        (None, Decision.NEEDS_REVIEW),  # unknown confidence is not good confidence
    ],
)
def test_confidence_threshold(confidence: float | None, expected: Decision) -> None:
    assert decision(run(), conf=confidence).decision is expected


def test_unknown_total_blocks_approval() -> None:
    d = decision(run(), total=None)
    assert d.decision is Decision.NEEDS_REVIEW
    assert TOTAL_UNKNOWN in {r.code for r in d.reasons}


def test_unknown_confidence_is_named_in_reasons() -> None:
    assert CONFIDENCE_UNKNOWN in {r.code for r in decision(run(), conf=None).reasons}


def test_a_check_that_did_not_run_blocks_approval() -> None:
    results = [r for r in run() if r.name is not CheckName.QUANTITY]
    d = decision(results)
    assert d.decision is Decision.NEEDS_REVIEW
    assert f"{CHECK_NOT_RUN}:{CheckName.QUANTITY}" in {r.code for r in d.reasons}


def test_empty_results_can_never_approve() -> None:
    assert decision([]).decision is Decision.NEEDS_REVIEW


def test_missing_po_is_explained_once_not_with_three_skipped_checks() -> None:
    d = decision(run(po=None))
    assert d.decision is Decision.NEEDS_REVIEW
    assert [r.code for r in d.reasons] == [str(CheckName.PO_EXISTS)]


def test_an_unreadable_invoice_shows_the_root_cause_not_a_wall_of_consequences() -> None:
    d = decide([], None, None, POLICY, [Reason(LLM_UNAVAILABLE, "model is down")])
    assert d.decision is Decision.NEEDS_REVIEW
    assert [r.code for r in d.reasons] == [LLM_UNAVAILABLE]


def test_root_cause_filtering_never_hides_a_real_failed_check() -> None:
    failing = [replace(r, passed=False) if r.name is CheckName.ARITHMETIC else r for r in run()]
    d = decide(failing, D("2300"), 1.0, POLICY, [Reason(LLM_UNAVAILABLE, "model is down")])
    assert {r.code for r in d.reasons} == {str(CheckName.ARITHMETIC), LLM_UNAVAILABLE}


def test_forced_review_reasons_block_approval_and_are_listed() -> None:
    d = decide(run(), D("2300"), 1.0, POLICY, [Reason(AGENT_STEP_LIMIT, "too many steps")])
    assert d.decision is Decision.NEEDS_REVIEW
    assert [r.code for r in d.reasons] == [AGENT_STEP_LIMIT]


def test_limits_come_from_config_not_from_constants() -> None:
    strict = PolicyConfig(auto_approve_limit=D("1000"), min_confidence=0.99)
    assert decide(run(), D("2300"), 1.0, strict).decision is Decision.NEEDS_REVIEW
    lenient = PolicyConfig(auto_approve_limit=D("99999"), min_confidence=0.0)
    assert decide(run(), D("2300"), 0.01, lenient).decision is Decision.AUTO_APPROVE


def test_vendor_none_cannot_be_auto_approved() -> None:
    assert decision(run(vendor=None)).decision is Decision.NEEDS_REVIEW


def test_could_not_check_for_duplicates_is_review_not_reject() -> None:
    """Regression: an inconclusive duplicate check once caused a REJECT."""
    results = run(vendor=None)
    dup = next(r for r in results if r.name is CheckName.EXACT_DUPLICATE)
    assert not dup.passed
    assert dup.inconclusive
    assert decision(results).decision is Decision.NEEDS_REVIEW


def test_a_conclusive_failure_still_rejects_even_alongside_inconclusive_ones() -> None:
    prior = PriorInvoice(1, "KS/24-25/0042", D("1"), dt.date(2020, 1, 1))
    results = run(prior_invoices=(prior,), invoice=replace(invoice(), invoice_date=None))
    assert any(r.inconclusive for r in results)  # possible_duplicate could not run
    assert decision(results).decision is Decision.REJECT  # but the exact duplicate was found


def test_there_is_no_input_that_approves_with_a_failed_check() -> None:
    """Property: for each single failing check, approval is impossible."""
    clean = run()
    for target in clean:
        broken = [replace(r, passed=False) if r is target else r for r in clean]
        assert decision(broken).decision is not Decision.AUTO_APPROVE, target.name


def test_vendor_factory_defaults_are_intra_state() -> None:
    assert vendor().state_code == "29"
