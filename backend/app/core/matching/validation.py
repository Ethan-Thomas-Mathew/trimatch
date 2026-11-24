"""Section 4.1: validation checks (GSTIN, tax type, arithmetic). Pure functions."""

from __future__ import annotations

import re
from decimal import Decimal

from app.core.matching.models import (
    MISSING,
    CheckResult,
    InvoiceData,
    MatchConfig,
    VendorData,
    money,
)
from app.schemas.enums import CheckName

GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_ZERO = Decimal("0")
_HALVES_TOLERANCE = Decimal("0.01")


def gstin_format(gstin: str | None) -> CheckResult:
    """15 characters in the official shape; the first 2 digits are the state code."""
    expected = "15 characters, e.g. 29ABCDE1234F1Z5"
    if gstin is None or not gstin.strip():
        return CheckResult(
            CheckName.GSTIN_FORMAT,
            False,
            expected,
            MISSING,
            "The invoice shows no GSTIN.",
            inconclusive=True,  # absent is not malformed: a human looks, we do not reject
        )
    value = gstin.strip()
    ok = GSTIN_PATTERN.match(value) is not None
    message = (
        "GSTIN has a valid format."
        if ok
        else f"GSTIN {value!r} is not a valid 15-character GSTIN ({len(value)} characters)."
    )
    return CheckResult(CheckName.GSTIN_FORMAT, ok, expected, value, message)


def gstin_matches_vendor(invoice_gstin: str | None, vendor: VendorData | None) -> CheckResult:
    if vendor is None:
        return CheckResult(
            CheckName.GSTIN_MATCHES_VENDOR,
            False,
            "a vendor in the vendor master",
            invoice_gstin or MISSING,
            "No vendor in the vendor master matches this invoice.",
        )
    actual = (invoice_gstin or "").strip()
    ok = actual == vendor.gstin
    message = (
        f"GSTIN matches vendor {vendor.name}."
        if ok
        else f"Invoice GSTIN {actual or MISSING} does not match the master record "
        f"for {vendor.name} ({vendor.gstin})."
    )
    return CheckResult(CheckName.GSTIN_MATCHES_VENDOR, ok, vendor.gstin, actual or MISSING, message)


def tax_type(
    supplier_state: str | None,
    buyer_state: str,
    cgst: Decimal | None,
    sgst: Decimal | None,
    igst: Decimal | None,
) -> CheckResult:
    """Same state: CGST + SGST in equal halves. Different states: IGST only.

    An invoice that charges no GST at all fails closed: we cannot verify the tax type, so a
    human looks at it (exempt supplies are rare enough that this is the safe direction).
    """
    c, s, i = cgst or _ZERO, sgst or _ZERO, igst or _ZERO
    actual = f"CGST {c:.2f}, SGST {s:.2f}, IGST {i:.2f}"
    if supplier_state is None:
        return CheckResult(
            CheckName.TAX_TYPE,
            False,
            "known supplier state",
            actual,
            "Cannot decide the tax type because the supplier's state is unknown.",
        )
    if c == s == i == _ZERO:
        return CheckResult(
            CheckName.TAX_TYPE,
            False,
            "CGST+SGST (same state) or IGST (different states)",
            actual,
            "The invoice charges no GST, so the tax type cannot be verified.",
        )
    if supplier_state == buyer_state:
        expected = "CGST + SGST in equal halves, no IGST (supplier and buyer in the same state)"
        ok = i == _ZERO and c > _ZERO and s > _ZERO and abs(c - s) <= _HALVES_TOLERANCE
        problem = "CGST and SGST must both be charged in equal halves and IGST must not be."
    else:
        expected = "IGST only (supplier and buyer in different states)"
        ok = i > _ZERO and c == _ZERO and s == _ZERO
        problem = "Only IGST may be charged; CGST/SGST must not be."
    message = "Tax type is correct for the supply." if ok else f"Wrong tax type. {problem}"
    return CheckResult(CheckName.TAX_TYPE, ok, expected, actual, message)


def arithmetic(invoice: InvoiceData, config: MatchConfig) -> CheckResult:
    """Each line is qty x price; lines + tax must equal the grand total (within tolerance)."""
    problems: list[str] = []

    for n, line in enumerate(invoice.lines, start=1):
        expected_total = line.qty * line.unit_price
        if abs(expected_total - line.line_total) > config.line_tolerance:
            problems.append(
                f"line {n}: {line.qty} x {line.unit_price:.2f} = {expected_total:.2f}, "
                f"but the invoice shows {line.line_total:.2f}"
            )

    lines_sum = sum((line.line_total for line in invoice.lines), start=_ZERO)
    tax_parts = sum((v or _ZERO for v in (invoice.cgst, invoice.sgst, invoice.igst)), start=_ZERO)

    if (
        invoice.subtotal is not None
        and abs(invoice.subtotal - lines_sum) > config.arithmetic_tolerance
    ):
        problems.append(
            f"subtotal {invoice.subtotal:.2f} differs from the sum of lines {lines_sum:.2f}"
        )
    if (
        invoice.tax_total is not None
        and abs(invoice.tax_total - tax_parts) > config.arithmetic_tolerance
    ):
        problems.append(
            f"tax total {invoice.tax_total:.2f} differs from CGST+SGST+IGST {tax_parts:.2f}"
        )

    expected_grand = lines_sum + tax_parts
    if invoice.grand_total is None:
        problems.append("the invoice shows no grand total")
    elif abs(expected_grand - invoice.grand_total) > config.arithmetic_tolerance:
        problems.append(
            f"lines {lines_sum:.2f} + tax {tax_parts:.2f} = {expected_grand:.2f}, "
            f"but the grand total is {invoice.grand_total:.2f}"
        )

    ok = not problems
    return CheckResult(
        CheckName.ARITHMETIC,
        ok,
        f"{expected_grand:.2f} (within ₹{config.arithmetic_tolerance})",
        money(invoice.grand_total),
        "Line totals and grand total add up." if ok else "Arithmetic error: " + "; ".join(problems),
    )
