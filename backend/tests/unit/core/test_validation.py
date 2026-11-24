"""Section 4.1 checks: GSTIN, tax type, arithmetic."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.core.matching import InvoiceLineData, MatchConfig
from app.core.matching.models import MISSING
from app.core.matching.validation import arithmetic, gstin_format, gstin_matches_vendor, tax_type
from app.schemas.enums import CheckName

from .factories import VENDOR_GSTIN, D, invoice, vendor

CFG = MatchConfig()


# ------------------------------------------------------------------ GSTIN format
@pytest.mark.parametrize(
    ("gstin", "ok"),
    [
        ("29AABCK1234F1ZR", True),
        ("  29AABCK1234F1ZR  ", True),  # surrounding whitespace is tolerated
        ("27AAPFU0939F1ZV", True),
        ("29aabck1234f1zr", False),  # lower case
        ("29AABCK1234F1XR", False),  # 14th character must be Z
        ("29AABCK1234F1Z", False),  # 14 characters
        ("29AABCK1234F1ZRR", False),  # 16 characters
        ("AAAABCK1234F1ZR", False),  # state code must be digits
        ("29AABCK1234F0ZR", False),  # entity code may not be 0
        ("", False),
    ],
)
def test_gstin_format(gstin: str, ok: bool) -> None:
    result = gstin_format(gstin)
    assert result.passed is ok
    assert result.name is CheckName.GSTIN_FORMAT


def test_gstin_missing_is_flagged_as_missing_not_malformed() -> None:
    result = gstin_format(None)
    assert not result.passed
    assert result.actual == MISSING
    assert result.inconclusive  # the policy relies on this to avoid rejecting


def test_gstin_matches_vendor() -> None:
    assert gstin_matches_vendor(VENDOR_GSTIN, vendor()).passed


def test_gstin_differs_from_vendor_master() -> None:
    result = gstin_matches_vendor("29AABCK9999F1ZR", vendor())
    assert not result.passed
    assert VENDOR_GSTIN in result.message


def test_gstin_check_fails_for_unknown_vendor_or_missing_gstin() -> None:
    assert not gstin_matches_vendor(VENDOR_GSTIN, None).passed
    assert not gstin_matches_vendor(None, vendor()).passed


# --------------------------------------------------------------------- tax type
def test_intra_state_with_equal_cgst_sgst_passes() -> None:
    assert tax_type("29", "29", D("150"), D("150"), None).passed


def test_intra_state_tolerates_a_paisa_in_the_halves() -> None:
    assert tax_type("29", "29", D("150.01"), D("150.00"), None).passed


def test_intra_state_billed_as_igst_fails() -> None:
    result = tax_type("29", "29", None, None, D("300"))
    assert not result.passed
    assert "Wrong tax type" in result.message


def test_intra_state_unequal_halves_fail() -> None:
    assert not tax_type("29", "29", D("200"), D("100"), None).passed


def test_inter_state_with_igst_passes() -> None:
    assert tax_type("36", "29", None, None, D("300")).passed


def test_inter_state_billed_as_cgst_sgst_fails() -> None:
    assert not tax_type("36", "29", D("150"), D("150"), None).passed


def test_inter_state_with_mixed_taxes_fails() -> None:
    assert not tax_type("36", "29", D("10"), None, D("290")).passed


def test_no_gst_at_all_fails_closed() -> None:
    result = tax_type("29", "29", None, None, None)
    assert not result.passed
    assert "cannot be verified" in result.message


def test_unknown_supplier_state_fails() -> None:
    assert not tax_type(None, "29", D("1"), D("1"), None).passed


# ------------------------------------------------------------------- arithmetic
def test_consistent_invoice_passes_arithmetic() -> None:
    assert arithmetic(invoice(), CFG).passed


def test_line_total_that_is_not_qty_times_price_fails() -> None:
    inv = invoice()
    bad = replace(inv.lines[0], line_total=D("1500.00"))
    result = arithmetic(replace(inv, lines=(bad, inv.lines[1])), CFG)
    assert not result.passed
    assert "line 1" in result.message


def test_grand_total_off_by_more_than_one_rupee_fails() -> None:
    result = arithmetic(replace(invoice(), grand_total=D("2301.50")), CFG)
    assert not result.passed
    assert "grand total" in result.message


def test_grand_total_off_by_rounding_up_to_one_rupee_passes() -> None:
    assert arithmetic(replace(invoice(), grand_total=D("2300.99")), CFG).passed
    assert arithmetic(replace(invoice(), grand_total=D("2301.00")), CFG).passed  # boundary


def test_tolerance_is_configurable() -> None:
    inv = replace(invoice(), grand_total=D("2305.00"))
    assert not arithmetic(inv, CFG).passed
    assert arithmetic(inv, MatchConfig(arithmetic_tolerance=D("10"))).passed


def test_subtotal_and_tax_total_must_agree_with_their_parts() -> None:
    assert not arithmetic(replace(invoice(), subtotal=D("1900.00")), CFG).passed
    assert not arithmetic(replace(invoice(), tax_total=D("350.00")), CFG).passed


def test_missing_grand_total_fails() -> None:
    assert not arithmetic(replace(invoice(), grand_total=None), CFG).passed


def test_missing_optional_totals_are_computed_not_trusted() -> None:
    inv = replace(invoice(), subtotal=None, tax_total=None)
    assert arithmetic(inv, CFG).passed


def test_fractional_quantities_round_within_a_paisa() -> None:
    line = InvoiceLineData("X", "Rope", Decimal("2.5"), Decimal("33.33"), Decimal("83.33"))
    inv = replace(
        invoice(),
        lines=(line,),
        subtotal=D("83.33"),
        cgst=D("7.50"),
        sgst=D("7.50"),
        tax_total=D("15.00"),
        grand_total=D("98.33"),
    )
    assert arithmetic(inv, CFG).passed  # 2.5 x 33.33 = 83.325, shown as 83.33


def test_invoice_with_no_lines_and_no_tax_is_consistent_but_caught_elsewhere() -> None:
    inv = replace(
        invoice(), lines=(), subtotal=None, cgst=None, sgst=None, tax_total=None, grand_total=D("0")
    )
    assert arithmetic(inv, CFG).passed  # line_coverage is what rejects an empty invoice
