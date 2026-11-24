"""Section 4.2 and 4.3 checks: PO matching, prices, quantities, duplicates."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace

from app.core.matching import InvoiceLineData, MatchConfig, PriorInvoice
from app.core.matching.duplicates import (
    exact_duplicate,
    normalise_invoice_number,
    possible_duplicate,
)
from app.core.matching.matching import (
    line_coverage,
    map_lines,
    po_exists,
    po_vendor_match,
    quantity,
    unit_price,
)

from .factories import D, invoice, po

CFG = MatchConfig()
FULL = {1: D("10"), 2: D("5")}


def mapped(**line_changes: object):  # type: ignore[no-untyped-def]
    inv = invoice()
    lines = (replace(inv.lines[0], **line_changes), inv.lines[1]) if line_changes else inv.lines
    return map_lines(lines, po())


# ------------------------------------------------------------------- line mapping
def test_lines_map_by_sku() -> None:
    pairs = map_lines(invoice().lines, po())
    assert [p.sku for _, p in pairs if p] == ["SKU-A", "SKU-B"]


def test_lines_fall_back_to_description_and_ignore_case_and_spacing() -> None:
    line = InvoiceLineData(None, "  ITEM   a ", D("1"), D("100"), D("100"))
    ((_, matched),) = map_lines((line,), po())
    assert matched is not None
    assert matched.sku == "SKU-A"


def test_unknown_item_does_not_map() -> None:
    line = InvoiceLineData("ZZZ", "Mystery goods", D("1"), D("1"), D("1"))
    ((_, matched),) = map_lines((line,), po())
    assert matched is None


# -------------------------------------------------------------------- PO checks
def test_po_exists() -> None:
    assert po_exists(po(), "PO-2024-0001").passed


def test_po_missing_in_database() -> None:
    result = po_exists(None, "PO-9999")
    assert not result.passed
    assert "PO-9999" in result.message


def test_invoice_quotes_no_po() -> None:
    result = po_exists(None, None)
    assert not result.passed
    assert "no PO number" in result.message


def test_po_must_belong_to_the_invoicing_vendor() -> None:
    assert po_vendor_match(po(vendor_id=1), 1).passed
    assert not po_vendor_match(po(vendor_id=2), 1).passed
    assert not po_vendor_match(po(), None).passed


# ----------------------------------------------------------------- line coverage
def test_every_line_covered() -> None:
    assert line_coverage(mapped()).passed


def test_uncovered_line_fails_and_is_named() -> None:
    line = InvoiceLineData("ZZZ", "Mystery goods", D("1"), D("1"), D("1"))
    result = line_coverage(map_lines((*invoice().lines, line), po()))
    assert not result.passed
    assert "Mystery goods" in result.message


def test_invoice_with_no_lines_fails_coverage() -> None:
    assert not line_coverage([]).passed


# -------------------------------------------------------------------- unit price
def test_exact_price_passes() -> None:
    assert unit_price(mapped(), CFG).passed


def test_half_percent_over_is_within_tolerance() -> None:
    assert unit_price(mapped(unit_price=D("100.50")), CFG).passed


def test_exactly_one_percent_is_still_within_tolerance() -> None:
    assert unit_price(mapped(unit_price=D("101.00")), CFG).passed


def test_just_over_one_percent_fails() -> None:
    result = unit_price(mapped(unit_price=D("101.01")), CFG)
    assert not result.passed
    assert "Item A" in result.message


def test_price_too_low_is_also_flagged() -> None:
    assert not unit_price(mapped(unit_price=D("90.00")), CFG).passed


def test_price_tolerance_is_configurable() -> None:
    assert unit_price(
        mapped(unit_price=D("105.00")), MatchConfig(price_tolerance_pct=D("5"))
    ).passed


def test_zero_po_price_only_matches_zero() -> None:
    free = replace(po().lines[0], unit_price=D("0"))
    order = replace(po(), lines=(free,))
    charged = InvoiceLineData("SKU-A", "Item A", D("1"), D("5"), D("5"))
    assert not unit_price(map_lines((charged,), order), CFG).passed
    free_line = replace(charged, unit_price=D("0"), line_total=D("0"))
    assert unit_price(map_lines((free_line,), order), CFG).passed


def test_unmapped_lines_are_not_priced() -> None:
    line = InvoiceLineData("ZZZ", "Mystery", D("1"), D("9999"), D("9999"))
    assert unit_price(map_lines((line,), po()), CFG).passed  # coverage reports it instead


# ---------------------------------------------------------------------- quantity
def test_full_receipt_covers_full_invoice() -> None:
    assert quantity(mapped(), FULL, {}).passed


def test_billing_more_than_received_fails() -> None:
    result = quantity(mapped(qty=D("12")), FULL, {})
    assert not result.passed
    assert "billed 12" in result.message


def test_partial_goods_receipt_limits_billing() -> None:
    assert not quantity(mapped(), {1: D("6"), 2: D("5")}, {}).passed
    assert quantity(mapped(qty=D("6")), {1: D("6"), 2: D("5")}, {}).passed


def test_multiple_grns_are_summed_by_the_caller_and_checked_as_one_total() -> None:
    received = {1: D("4") + D("6"), 2: D("5")}  # two receipts of 4 and 6
    assert quantity(mapped(), received, {}).passed


def test_quantity_already_invoiced_is_deducted() -> None:
    result = quantity(mapped(), FULL, {1: D("3")})
    assert not result.passed
    assert "already invoiced 3" in result.message
    assert quantity(mapped(qty=D("7")), FULL, {1: D("3")}).passed  # 10 - 3 = 7 available


def test_nothing_received_means_nothing_can_be_billed() -> None:
    assert not quantity(mapped(), {}, {}).passed


def test_zero_or_negative_quantity_fails() -> None:
    assert not quantity(mapped(qty=D("0")), FULL, {}).passed
    assert not quantity(mapped(qty=D("-1")), FULL, {}).passed


def test_two_invoice_lines_for_one_po_line_are_added_together() -> None:
    inv = invoice()
    split = (replace(inv.lines[0], qty=D("6")), replace(inv.lines[0], qty=D("6")), inv.lines[1])
    assert not quantity(map_lines(split, po()), FULL, {}).passed  # 12 > 10


# --------------------------------------------------------------------- duplicates
PRIOR = PriorInvoice(1, "KS/24-25/0042", D("2300.00"), dt.date(2024, 7, 1))


def test_invoice_number_normalisation() -> None:
    assert normalise_invoice_number(" KS / 24-25 / 0042 ") == normalise_invoice_number(
        "ks/24-25/0042"
    )


def test_exact_duplicate_detected_ignoring_case_and_spaces() -> None:
    assert not exact_duplicate(1, "ks / 24-25 / 0042", [PRIOR]).passed


def test_same_number_from_a_different_vendor_is_not_a_duplicate() -> None:
    assert exact_duplicate(2, "KS/24-25/0042", [PRIOR]).passed


def test_different_number_is_not_an_exact_duplicate() -> None:
    assert exact_duplicate(1, "KS/24-25/0043", [PRIOR]).passed


def test_duplicate_check_fails_closed_without_vendor_or_number() -> None:
    assert not exact_duplicate(None, "KS/1", [PRIOR]).passed
    assert not exact_duplicate(1, None, [PRIOR]).passed
    assert not exact_duplicate(1, "   ", [PRIOR]).passed


def test_possible_duplicate_same_vendor_total_within_window() -> None:
    result = possible_duplicate(1, D("2300.00"), dt.date(2024, 7, 20), [PRIOR], 30)
    assert not result.passed


def test_possible_duplicate_window_boundary() -> None:
    assert not possible_duplicate(1, D("2300.00"), dt.date(2024, 7, 31), [PRIOR], 30).passed
    assert possible_duplicate(1, D("2300.00"), dt.date(2024, 8, 1), [PRIOR], 30).passed


def test_possible_duplicate_needs_the_same_total_and_vendor() -> None:
    assert possible_duplicate(1, D("2300.01"), dt.date(2024, 7, 2), [PRIOR], 30).passed
    assert possible_duplicate(2, D("2300.00"), dt.date(2024, 7, 2), [PRIOR], 30).passed


def test_possible_duplicate_fails_closed_when_inputs_are_missing() -> None:
    assert not possible_duplicate(1, None, dt.date(2024, 7, 2), [PRIOR], 30).passed
    assert not possible_duplicate(1, D("1"), None, [PRIOR], 30).passed
    assert not possible_duplicate(None, D("1"), dt.date(2024, 7, 2), [PRIOR], 30).passed
