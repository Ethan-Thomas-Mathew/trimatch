"""The synthetic world must agree with its own labels, and be reproducible."""

from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest

from app.schemas.enums import CheckName, Decision
from render import amount_in_words, fmt_indian
from world import (
    AUTO_APPROVE_LIMIT,
    DEFAULT_SEED,
    GSTIN_RE,
    N_POS,
    SCENARIO_COUNTS,
    World,
    build_world,
    gstin_check_char,
    make_gstin,
)


@pytest.fixture(scope="module")
def world() -> World:
    return build_world(DEFAULT_SEED)


def test_dataset_shape_matches_spec(world: World) -> None:
    assert len(world.vendors) == 20
    assert len(world.pos) == N_POS == 150
    assert len(world.invoices) == 100
    assert Counter(i.scenario for i in world.invoices) == Counter(SCENARIO_COUNTS)
    assert sum(i.scanned for i in world.invoices) == 8
    assert sum(i.injection_text is not None for i in world.invoices) == 5
    assert len({i.layout for i in world.invoices}) >= 3


def test_every_vendor_gstin_is_valid_and_unique(world: World) -> None:
    gstins = [v.party.gstin for v in world.vendors]
    assert all(GSTIN_RE.match(g) for g in gstins)
    assert len(set(gstins)) == len(gstins)
    assert all(v.party.gstin[:2] == v.party.state_code for v in world.vendors)
    assert len({v.party.state_code for v in world.vendors}) >= 5


def test_known_real_gstin_checksum() -> None:
    # 27AAPFU0939F1ZV is the widely published sample GSTIN; V is its checksum character.
    assert gstin_check_char("27AAPFU0939F1Z") == "V"
    assert make_gstin("27", "AAPFU0939F") == "27AAPFU0939F1ZV"


def test_decision_labels_follow_the_policy_rules(world: World) -> None:
    for inv in world.invoices:
        if inv.scenario == "exact_duplicate" or inv.scenario == "gstin_invalid_format":
            assert inv.expected_decision == Decision.REJECT
        elif inv.scenario == "clean":
            expected = (
                Decision.NEEDS_REVIEW
                if inv.grand_total > AUTO_APPROVE_LIMIT
                else (Decision.AUTO_APPROVE)
            )
            assert inv.expected_decision == expected
        else:
            assert inv.expected_decision == Decision.NEEDS_REVIEW


def test_only_clean_invoices_are_expected_to_auto_approve(world: World) -> None:
    for inv in world.invoices:
        if inv.expected_decision == Decision.AUTO_APPROVE:
            assert inv.scenario == "clean"
            assert not inv.must_fail
        else:
            assert inv.must_fail or inv.over_limit


def test_injection_invoices_all_contain_a_real_problem(world: World) -> None:
    injected = [i for i in world.invoices if i.scenario == "prompt_injection"]
    assert len(injected) == 5
    for inv in injected:
        assert inv.must_fail
        assert inv.expected_decision == Decision.NEEDS_REVIEW
        assert inv.injection_text
    assert {i.injection_hidden for i in injected} == {True, False}


def test_edge_cases_are_covered(world: World) -> None:
    # Multiple GRNs on one PO, partial receipts, an inter-state and an intra-state vendor.
    assert any(len(p.grns) > 1 for p in world.pos)
    assert any(
        any(p.received(i) < ln.qty for i, ln in enumerate(p.lines)) for p in world.pos if p.grns
    )
    assert any(not p.grns for p in world.pos)
    assert {i.intra_state_billing for i in world.invoices} == {True, False}
    # Prices inside the 1% tolerance are labelled clean.
    assert any("within 1% tolerance" in n for i in world.invoices for n in i.notes)


def test_exact_duplicates_share_vendor_and_number_with_history(world: World) -> None:
    history = {(h.vendor.index, h.invoice_number) for h in world.historical_invoices}
    exact = [i for i in world.invoices if i.scenario == "exact_duplicate"]
    assert len(exact) == 5
    assert all((i.vendor.index, i.invoice_number) in history for i in exact)
    assert all(CheckName.EXACT_DUPLICATE in i.must_fail for i in exact)


def test_arithmetic_errors_really_are_wrong(world: World) -> None:
    bad = [i for i in world.invoices if i.scenario == "gst_arithmetic"]
    assert len(bad) == 2
    for inv in bad:
        line_ok = all(ln.line_total == ln.qty * ln.unit_price for ln in inv.lines)
        sums_ok = inv.subtotal + inv.tax_total == inv.grand_total
        assert not (line_ok and sums_ok)


def test_same_seed_gives_same_world_and_other_seed_differs() -> None:
    a = [i.ground_truth() for i in build_world(7).invoices]
    b = [i.ground_truth() for i in build_world(7).invoices]
    c = [i.ground_truth() for i in build_world(8).invoices]
    assert a == b
    assert a != c


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "0.00"),
        ("999.5", "999.50"),
        ("1000", "1,000.00"),
        ("123456.78", "1,23,456.78"),
        ("12345678.9", "1,23,45,678.90"),
    ],
)
def test_indian_grouping(value: str, expected: str) -> None:
    assert fmt_indian(Decimal(value)) == expected


def test_amount_in_words() -> None:
    assert amount_in_words(Decimal("41639.50")) == (
        "Rupees Forty One Thousand Six Hundred Thirty Nine and Fifty Paise Only"
    )
    assert amount_in_words(Decimal("100000")) == "Rupees One Lakh Only"


@pytest.fixture(scope="module")
def generated(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, dict[str, object]]:
    from generate_dataset import generate

    out = tmp_path_factory.mktemp("dataset")
    return out, generate(build_world(DEFAULT_SEED), out)


def test_generated_files_and_ground_truth(generated: tuple[Path, dict[str, object]]) -> None:
    out, manifest = generated
    pdfs = sorted((out / "invoices").glob("*.pdf"))
    truths = sorted((out / "ground_truth").glob("*.json"))
    assert len(pdfs) == len(truths) == 100
    assert manifest["invoice_count"] == 100
    sample = json.loads(truths[0].read_text("utf-8"))
    assert set(sample) >= {"invoice_id", "expected_decision", "fields", "scanned", "layout"}
    assert {"invoice_number", "lines", "grand_total", "vendor_gstin"} <= set(sample["fields"])
    # Money is serialised as exact decimal strings, never floats.
    assert isinstance(sample["fields"]["grand_total"], str)


def test_scanned_pdfs_have_no_text_and_injections_are_in_the_text_layer(
    generated: tuple[Path, dict[str, object]],
) -> None:
    import pdfplumber

    out, _ = generated
    for gt_path in sorted((out / "ground_truth").glob("*.json")):
        gt = json.loads(gt_path.read_text("utf-8"))
        with pdfplumber.open(out / gt["pdf"]) as pdf:
            text = pdf.pages[0].extract_text() or ""
        if gt["scanned"]:
            assert text.strip() == "", gt["invoice_id"]
        else:
            assert gt["fields"]["invoice_number"] in text, gt["invoice_id"]
            if gt["injection"]:
                assert gt["injection"]["text"][:25] in text, gt["invoice_id"]


def test_generation_is_byte_for_byte_reproducible(
    generated: tuple[Path, dict[str, object]], tmp_path: Path
) -> None:
    from generate_dataset import generate

    _, first = generated
    second = generate(build_world(DEFAULT_SEED), tmp_path)
    assert first["files"] == second["files"]
