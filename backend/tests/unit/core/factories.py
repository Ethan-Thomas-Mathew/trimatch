"""Builders for a small, fully consistent clean scenario that tests then tweak.

Clean scenario: buyer and vendor are both in state 29 (intra-state), PO has two lines that are
fully received, nothing invoiced before, and the invoice matches to the paisa.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from app.core.matching import (
    InvoiceData,
    InvoiceLineData,
    MatchInputs,
    POLineData,
    PurchaseOrderData,
    VendorData,
)

D = Decimal
BUYER_STATE = "29"
VENDOR_GSTIN = "29AABCK1234F1ZR"


def vendor(state: str = "29", gstin: str = VENDOR_GSTIN) -> VendorData:
    return VendorData(id=1, name="Kaveri Supplies", gstin=gstin, state_code=state)


def po(vendor_id: int = 1) -> PurchaseOrderData:
    return PurchaseOrderData(
        po_number="PO-2024-0001",
        vendor_id=vendor_id,
        lines=(
            POLineData(1, "SKU-A", "Item A", D("10"), D("100.00"), D("18")),
            POLineData(2, "SKU-B", "Item B", D("5"), D("200.00"), D("12")),
        ),
    )


def invoice() -> InvoiceData:
    return InvoiceData(
        vendor_gstin=VENDOR_GSTIN,
        invoice_number="KS/24-25/0042",
        invoice_date=dt.date(2024, 7, 1),
        po_number="PO-2024-0001",
        lines=(
            InvoiceLineData("SKU-A", "Item A", D("10"), D("100.00"), D("1000.00")),
            InvoiceLineData("SKU-B", "Item B", D("5"), D("200.00"), D("1000.00")),
        ),
        subtotal=D("2000.00"),
        cgst=D("150.00"),
        sgst=D("150.00"),
        igst=None,
        tax_total=D("300.00"),
        grand_total=D("2300.00"),
    )


def inputs(**overrides: object) -> MatchInputs:
    base: dict[str, object] = {
        "invoice": invoice(),
        "buyer_state_code": BUYER_STATE,
        "vendor": vendor(),
        "po": po(),
        "received": {1: D("10"), 2: D("5")},
        "already_invoiced": {},
        "prior_invoices": (),
    }
    base.update(overrides)
    return MatchInputs(**base)  # type: ignore[arg-type]
