"""Section 4.3: duplicate checks. Pure functions."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal

from app.core.matching.models import MISSING, CheckResult, PriorInvoice, money
from app.schemas.enums import CheckName


def normalise_invoice_number(number: str) -> str:
    """'ABC / 24-25 / 0042' and 'abc/24-25/0042' are the same invoice."""
    return "".join(number.split()).casefold()


def exact_duplicate(
    vendor_id: int | None, invoice_number: str | None, priors: Sequence[PriorInvoice]
) -> CheckResult:
    """Same vendor and same invoice number as an earlier invoice -> REJECT (policy)."""
    expected = "no earlier invoice with the same vendor and number"
    if vendor_id is None or not invoice_number or not invoice_number.strip():
        return CheckResult(
            CheckName.EXACT_DUPLICATE,
            False,
            expected,
            invoice_number or MISSING,
            "Cannot check for duplicates: the vendor or the invoice number is unknown.",
            inconclusive=True,
        )
    wanted = normalise_invoice_number(invoice_number)
    hits = [
        p
        for p in priors
        if p.vendor_id == vendor_id
        and p.invoice_number is not None
        and normalise_invoice_number(p.invoice_number) == wanted
    ]
    ok = not hits
    return CheckResult(
        CheckName.EXACT_DUPLICATE,
        ok,
        expected,
        f"{len(hits)} earlier invoice(s) with number {invoice_number}",
        "No earlier invoice has this number."
        if ok
        else f"Invoice {invoice_number} from this vendor was already received.",
    )


def possible_duplicate(
    vendor_id: int | None,
    grand_total: Decimal | None,
    invoice_date: dt.date | None,
    priors: Sequence[PriorInvoice],
    window_days: int,
) -> CheckResult:
    """Same vendor and same total within the window -> NEEDS_REVIEW."""
    expected = f"no invoice from this vendor with the same total within {window_days} days"
    if vendor_id is None or grand_total is None or invoice_date is None:
        return CheckResult(
            CheckName.POSSIBLE_DUPLICATE,
            False,
            expected,
            money(grand_total),
            "Cannot check for a possible duplicate: vendor, total or date is unknown.",
            inconclusive=True,
        )
    hits = [
        p
        for p in priors
        if p.vendor_id == vendor_id
        and p.grand_total == grand_total
        and p.invoice_date is not None
        and abs((p.invoice_date - invoice_date).days) <= window_days
    ]
    ok = not hits
    return CheckResult(
        CheckName.POSSIBLE_DUPLICATE,
        ok,
        expected,
        f"{len(hits)} similar invoice(s)",
        "No similar earlier invoice found."
        if ok
        else f"{len(hits)} earlier invoice(s) from this vendor have the same total "
        f"({grand_total:.2f}) within {window_days} days.",
    )
