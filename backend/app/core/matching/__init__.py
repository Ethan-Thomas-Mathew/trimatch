"""Deterministic three-way-match rules (build plan section 4)."""

from app.core.matching.engine import run_three_way_match
from app.core.matching.models import (
    MISSING,
    CheckResult,
    InvoiceData,
    InvoiceLineData,
    MatchConfig,
    MatchInputs,
    POLineData,
    PriorInvoice,
    PurchaseOrderData,
    VendorData,
)

__all__ = [
    "MISSING",
    "CheckResult",
    "InvoiceData",
    "InvoiceLineData",
    "MatchConfig",
    "MatchInputs",
    "POLineData",
    "PriorInvoice",
    "PurchaseOrderData",
    "VendorData",
    "run_three_way_match",
]
