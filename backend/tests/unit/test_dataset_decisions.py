"""Run the real rules and policy over all 100 dataset invoices, assuming perfect extraction.

This isolates the deterministic half of the system: given correct extracted fields, do the
rules reach the decision the dataset says is correct? It needs no database and no model.
"""

from __future__ import annotations

from collections import Counter
from decimal import Decimal

import pytest

from app.core.matching import (
    CheckResult,
    InvoiceData,
    InvoiceLineData,
    MatchConfig,
    MatchInputs,
    POLineData,
    PriorInvoice,
    PurchaseOrderData,
    VendorData,
    run_three_way_match,
)
from app.core.policy import PolicyConfig, decide
from app.schemas.enums import CheckName, Decision
from world import AUTO_APPROVE_LIMIT, DEFAULT_SEED, InvoiceSpec, World, build_world

CFG = MatchConfig()
POLICY = PolicyConfig(auto_approve_limit=AUTO_APPROVE_LIMIT, min_confidence=0.9)
DUPLICATE_CHECKS = {CheckName.EXACT_DUPLICATE, CheckName.POSSIBLE_DUPLICATE}


def _zero_to_none(value: Decimal) -> Decimal | None:
    return None if value == 0 else value


def build_inputs(world: World, inv: InvoiceSpec) -> MatchInputs:
    """What the agent's tools would gather for this invoice from the seeded database."""
    vendors = {
        v.party.name: VendorData(v.index + 1, v.party.name, v.party.gstin, v.party.state_code)
        for v in world.vendors
    }
    vendor = vendors[inv.vendor.party.name]  # looked up by the printed vendor name

    po_data: PurchaseOrderData | None = None
    received: dict[int, Decimal] = {}
    already: dict[int, Decimal] = {}
    po_numbers = {p.po_number: p for p in world.pos}
    if inv.printed_po_number in po_numbers:
        po = po_numbers[inv.printed_po_number or ""]
        base = world.pos.index(po) * 100  # stable unique ids for this test's PO lines
        po_data = PurchaseOrderData(
            po.po_number,
            po.vendor.index + 1,
            tuple(
                POLineData(base + i, ln.sku, ln.description, ln.qty, ln.unit_price, ln.tax_rate)
                for i, ln in enumerate(po.lines)
            ),
        )
        for i in range(len(po.lines)):
            received[base + i] = po.received(i)
        for hist in world.historical_invoices:
            if hist.po is po:
                for ln in hist.lines:
                    assert ln.po_line_index is not None
                    key = base + ln.po_line_index
                    already[key] = already.get(key, Decimal(0)) + ln.qty

    priors = tuple(
        PriorInvoice(h.vendor.index + 1, h.invoice_number, h.grand_total, h.invoice_date)
        for h in world.historical_invoices
    )
    gt = inv.ground_truth()["fields"]
    invoice = InvoiceData(
        vendor_gstin=gt["vendor_gstin"],
        invoice_number=gt["invoice_number"],
        invoice_date=inv.invoice_date,
        po_number=gt["po_number"],
        lines=tuple(
            InvoiceLineData(
                ln["sku"],
                ln["description"],
                Decimal(ln["qty"]),
                Decimal(ln["unit_price"]),
                Decimal(ln["line_total"]),
            )
            for ln in gt["lines"]
        ),
        subtotal=Decimal(gt["subtotal"]),
        cgst=_zero_to_none(Decimal(gt["cgst"])),
        sgst=_zero_to_none(Decimal(gt["sgst"])),
        igst=_zero_to_none(Decimal(gt["igst"])),
        tax_total=Decimal(gt["tax_total"]),
        grand_total=Decimal(gt["grand_total"]),
    )
    return MatchInputs(
        invoice=invoice,
        buyer_state_code=world.buyer.state_code,
        vendor=vendor,
        po=po_data,
        received=received,
        already_invoiced=already,
        prior_invoices=priors,
    )


@pytest.fixture(scope="module")
def outcomes() -> list[tuple[InvoiceSpec, list[CheckResult], Decision]]:
    world = build_world(DEFAULT_SEED)
    rows = []
    for inv in world.invoices:
        results = run_three_way_match(build_inputs(world, inv), CFG)
        d = decide(results, inv.grand_total, 1.0, POLICY)
        rows.append((inv, results, d.decision))
    return rows


def test_every_invoice_gets_the_decision_the_dataset_expects(
    outcomes: list[tuple[InvoiceSpec, list[CheckResult], Decision]],
) -> None:
    wrong = [
        f"{inv.invoice_id} ({inv.scenario}): expected {inv.expected_decision}, got {got}"
        for inv, _, got in outcomes
        if got != inv.expected_decision
    ]
    assert not wrong, "\n".join(wrong)


def test_false_approval_rate_is_zero(
    outcomes: list[tuple[InvoiceSpec, list[CheckResult], Decision]],
) -> None:
    """The headline safety metric: nothing that should not be approved is approved."""
    approved_wrongly = [
        inv.invoice_id
        for inv, _, got in outcomes
        if got is Decision.AUTO_APPROVE and inv.expected_decision is not Decision.AUTO_APPROVE
    ]
    assert approved_wrongly == []


def test_all_five_prompt_injection_invoices_are_not_approved(
    outcomes: list[tuple[InvoiceSpec, list[CheckResult], Decision]],
) -> None:
    injected = [(inv, got) for inv, _, got in outcomes if inv.injection_text]
    assert len(injected) == 5
    assert all(got is not Decision.AUTO_APPROVE for _, got in injected)


def test_failed_checks_match_the_labelled_ones(
    outcomes: list[tuple[InvoiceSpec, list[CheckResult], Decision]],
) -> None:
    for inv, results, _ in outcomes:
        failed = {r.name for r in results if not r.passed}
        expected = set(inv.must_fail)
        assert expected <= failed, (inv.invoice_id, inv.scenario, failed)
        if inv.scenario not in {"exact_duplicate", "possible_duplicate"}:
            # Duplicates legitimately trip a second duplicate check; everything else is exact.
            assert failed == expected, (inv.invoice_id, inv.scenario, failed, expected)


def test_clean_invoices_pass_every_check_including_the_tolerance_edge_cases(
    outcomes: list[tuple[InvoiceSpec, list[CheckResult], Decision]],
) -> None:
    clean = [results for inv, results, _ in outcomes if inv.scenario == "clean"]
    assert len(clean) == 58
    assert all(r.passed for results in clean for r in results)


def test_decision_distribution_matches_the_dataset(
    outcomes: list[tuple[InvoiceSpec, list[CheckResult], Decision]],
) -> None:
    counts = Counter(str(got) for *_, got in outcomes)
    assert counts == {"AUTO_APPROVE": 48, "NEEDS_REVIEW": 45, "REJECT": 7}


def test_the_engine_is_deterministic(
    outcomes: list[tuple[InvoiceSpec, list[CheckResult], Decision]],
) -> None:
    world = build_world(DEFAULT_SEED)
    inv = world.invoices[3]
    first = run_three_way_match(build_inputs(world, inv), CFG)
    second = run_three_way_match(build_inputs(world, inv), CFG)
    assert first == second
