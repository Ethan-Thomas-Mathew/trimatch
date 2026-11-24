"""Assemble the deterministic engine's inputs from trusted sources only.

Important safety property: the inputs to ``run_three_way_match`` come from our own database and
from the stored extraction, never from values an LLM passed along. The model chooses which tools
to call, but it cannot influence what the rules are fed.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.core.matching import MatchInputs
from app.db import queries


def assemble_match_inputs(session: Session, invoice_id: int, settings: Settings) -> MatchInputs:
    extraction = queries.latest_extraction(session, invoice_id)
    if extraction is None:
        raise queries.IncompleteInvoiceError("no extraction exists for this invoice")
    invoice = queries.to_invoice_data(extraction)

    vendor = queries.find_vendor(session, invoice.vendor_gstin, extraction.vendor_name.value)
    po = queries.get_purchase_order(session, invoice.po_number)
    received = queries.get_received_quantities(session, po.po_number) if po else {}
    already = queries.get_invoiced_quantities(session, po.po_number, invoice_id) if po else {}
    priors = queries.get_prior_invoices(session, vendor.id, invoice_id) if vendor else []

    return MatchInputs(
        invoice=invoice,
        buyer_state_code=settings.buyer_state_code,
        vendor=vendor,
        po=po,
        received=received,
        already_invoiced=already,
        prior_invoices=priors,
    )
