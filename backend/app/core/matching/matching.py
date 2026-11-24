"""Section 4.2: matching checks against the purchase order and goods receipts. Pure functions."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

from app.core.matching.models import (
    MISSING,
    CheckResult,
    InvoiceLineData,
    MatchConfig,
    POLineData,
    PurchaseOrderData,
)
from app.schemas.enums import CheckName

LineMap = list[tuple[InvoiceLineData, POLineData | None]]
_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def _norm(text: str) -> str:
    return " ".join(text.split()).casefold()


def map_lines(invoice_lines: tuple[InvoiceLineData, ...], po: PurchaseOrderData) -> LineMap:
    """Pair each invoice line with its PO line: by item code first, then exact description."""
    by_sku = {_norm(line.sku): line for line in reversed(po.lines)}  # first PO line wins
    by_desc = {_norm(line.description): line for line in reversed(po.lines)}
    mapped: LineMap = []
    for line in invoice_lines:
        match: POLineData | None = None
        if line.sku and _norm(line.sku) in by_sku:
            match = by_sku[_norm(line.sku)]
        elif _norm(line.description) in by_desc:
            match = by_desc[_norm(line.description)]
        mapped.append((line, match))
    return mapped


def po_exists(po: PurchaseOrderData | None, printed_po_number: str | None) -> CheckResult:
    expected = printed_po_number or "a PO number on the invoice"
    if po is not None:
        return CheckResult(
            CheckName.PO_EXISTS, True, expected, po.po_number, f"PO {po.po_number} exists."
        )
    message = (
        "The invoice quotes no PO number."
        if not printed_po_number
        else f"PO {printed_po_number} does not exist in the system."
    )
    return CheckResult(CheckName.PO_EXISTS, False, expected, MISSING, message)


def po_vendor_match(po: PurchaseOrderData, vendor_id: int | None) -> CheckResult:
    ok = vendor_id is not None and po.vendor_id == vendor_id
    expected = f"vendor id {po.vendor_id} (owner of {po.po_number})"
    actual = MISSING if vendor_id is None else f"vendor id {vendor_id}"
    if ok:
        message = f"PO {po.po_number} belongs to the invoicing vendor."
    elif vendor_id is None:
        message = "The invoicing vendor is unknown, so the PO owner cannot be confirmed."
    else:
        message = f"PO {po.po_number} belongs to a different vendor than the one invoicing."
    return CheckResult(CheckName.PO_VENDOR_MATCH, ok, expected, actual, message)


def line_coverage(mapped: LineMap) -> CheckResult:
    if not mapped:
        return CheckResult(
            CheckName.LINE_COVERAGE, False, "at least one invoice line", "0 lines",
            "The invoice has no line items.",
        )  # fmt: skip
    missing = [line.description for line, po_line in mapped if po_line is None]
    ok = not missing
    return CheckResult(
        CheckName.LINE_COVERAGE,
        ok,
        "every invoice line maps to a PO line",
        f"{len(mapped) - len(missing)} of {len(mapped)} mapped",
        "Every invoice line matches a PO line."
        if ok
        else "These invoice lines are not on the PO: " + "; ".join(missing),
    )


def unit_price(mapped: LineMap, config: MatchConfig) -> CheckResult:
    """Invoice price within the tolerance (default 1%) of the PO price, per line."""
    bad: list[str] = []
    for line, po_line in mapped:
        if po_line is None:
            continue
        if po_line.unit_price == _ZERO:
            off = line.unit_price != _ZERO
            detail = f"PO price is 0 but invoice charges {line.unit_price:.2f}"
        else:
            pct = abs(line.unit_price - po_line.unit_price) / po_line.unit_price * _HUNDRED
            off = pct > config.price_tolerance_pct
            detail = (
                f"{line.description}: invoice {line.unit_price:.2f} vs PO "
                f"{po_line.unit_price:.2f} ({pct:.2f}% difference)"
            )
        if off:
            bad.append(detail)
    ok = not bad
    return CheckResult(
        CheckName.UNIT_PRICE,
        ok,
        f"within {config.price_tolerance_pct}% of the PO price",
        "all lines within tolerance" if ok else f"{len(bad)} line(s) outside tolerance",
        "Unit prices match the PO." if ok else "Price differs from the PO: " + "; ".join(bad),
    )


def quantity(
    mapped: LineMap,
    received: Mapping[int, Decimal],
    already_invoiced: Mapping[int, Decimal],
) -> CheckResult:
    """Invoiced qty <= received (all GRNs) minus qty already invoiced, per PO line."""
    bad: list[str] = []
    totals: dict[int, Decimal] = {}
    descriptions: dict[int, str] = {}

    for line, po_line in mapped:
        if line.qty <= _ZERO:
            bad.append(f"{line.description}: quantity {line.qty} is not positive")
        if po_line is None:
            continue
        totals[po_line.id] = totals.get(po_line.id, _ZERO) + line.qty
        descriptions[po_line.id] = line.description

    for po_line_id, billed in totals.items():
        got = received.get(po_line_id, _ZERO)
        before = already_invoiced.get(po_line_id, _ZERO)
        available = got - before
        if billed > available:
            bad.append(
                f"{descriptions[po_line_id]}: billed {billed}, but only {available} available "
                f"(received {got}, already invoiced {before})"
            )

    ok = not bad
    return CheckResult(
        CheckName.QUANTITY,
        ok,
        "billed quantity <= received minus already invoiced",
        "all lines within received quantity" if ok else f"{len(bad)} problem(s)",
        "Quantities are covered by goods receipts."
        if ok
        else "Over-billed or invalid quantity: " + "; ".join(bad),
    )
