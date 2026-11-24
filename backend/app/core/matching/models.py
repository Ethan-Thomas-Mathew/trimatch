"""Plain data types for the matching engine.

Everything here is a frozen dataclass of plain Python values. The engine never sees a
database session, an ORM object, an HTTP client or an LLM, so every rule is a pure
function that is trivial to unit-test and impossible to talk into anything.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from app.schemas.enums import CheckName

# Shown as the "actual" value when the invoice simply does not state something.
MISSING = "<missing>"


def money(value: Decimal | None) -> str:
    return MISSING if value is None else f"{value:.2f}"


@dataclass(frozen=True)
class CheckResult:
    """The outcome of one deterministic check, with enough detail to explain it to a human."""

    name: CheckName
    passed: bool
    expected: str
    actual: str
    message: str
    # True when the check could not be evaluated because an input was missing. It is still a
    # failure (never a pass), but it is "could not check", not "checked and found a problem",
    # so the policy will not REJECT on it.
    inconclusive: bool = False


@dataclass(frozen=True)
class MatchConfig:
    """Tolerances. Defaults match build plan section 4; production values come from settings."""

    price_tolerance_pct: Decimal = Decimal("1")
    arithmetic_tolerance: Decimal = Decimal("1")  # rupees, for the sum of lines + tax = total
    line_tolerance: Decimal = Decimal("0.01")  # rupees, for qty x price = line total
    duplicate_window_days: int = 30


@dataclass(frozen=True)
class InvoiceLineData:
    sku: str | None
    description: str
    qty: Decimal
    unit_price: Decimal
    line_total: Decimal


@dataclass(frozen=True)
class InvoiceData:
    """What was extracted from the invoice. Header values may be missing (``None``)."""

    vendor_gstin: str | None
    invoice_number: str | None
    invoice_date: dt.date | None
    po_number: str | None
    lines: tuple[InvoiceLineData, ...]
    subtotal: Decimal | None
    cgst: Decimal | None
    sgst: Decimal | None
    igst: Decimal | None
    tax_total: Decimal | None
    grand_total: Decimal | None


@dataclass(frozen=True)
class VendorData:
    id: int
    name: str
    gstin: str
    state_code: str


@dataclass(frozen=True)
class POLineData:
    id: int
    sku: str
    description: str
    qty: Decimal
    unit_price: Decimal
    tax_rate: Decimal


@dataclass(frozen=True)
class PurchaseOrderData:
    po_number: str
    vendor_id: int
    lines: tuple[POLineData, ...]


@dataclass(frozen=True)
class PriorInvoice:
    """An earlier invoice already in the system (never the one being checked)."""

    vendor_id: int
    invoice_number: str | None
    grand_total: Decimal | None
    invoice_date: dt.date | None


@dataclass(frozen=True)
class MatchInputs:
    """Everything the engine needs, gathered beforehand by the caller (the agent's tools)."""

    invoice: InvoiceData
    buyer_state_code: str
    vendor: VendorData | None
    po: PurchaseOrderData | None
    received: Mapping[int, Decimal] = field(default_factory=dict)  # po_line_id -> qty received
    already_invoiced: Mapping[int, Decimal] = field(default_factory=dict)  # po_line_id -> qty
    prior_invoices: Sequence[PriorInvoice] = ()
