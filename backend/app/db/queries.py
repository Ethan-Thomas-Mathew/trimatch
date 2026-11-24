"""Read-only lookups that turn database rows into the plain data types of ``app.core``.

Nothing in this module writes. The agent's tools run these inside a database transaction that
is itself declared READ ONLY (see ``app.agent.tools``), so even a bug here could not modify data.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.matching import (
    InvoiceData,
    InvoiceLineData,
    POLineData,
    PriorInvoice,
    PurchaseOrderData,
    VendorData,
)
from app.db.models import (
    ExtractionRun,
    GoodsReceipt,
    GRNLine,
    Invoice,
    InvoiceLine,
    POLine,
    PurchaseOrder,
    Vendor,
)
from app.schemas.enums import InvoiceStatus
from app.schemas.extraction import InvoiceExtraction

# Invoices in these states have claimed their goods and are visible to duplicate checks.
COUNTED_STATUSES = (
    InvoiceStatus.AUTO_APPROVED,
    InvoiceStatus.APPROVED,
    InvoiceStatus.NEEDS_REVIEW,
)
_ZERO = Decimal(0)


class IncompleteInvoiceError(Exception):
    """The extraction lacks values the checks cannot do without (for example a line quantity)."""


def _vendor_data(v: Vendor) -> VendorData:
    return VendorData(id=v.id, name=v.name, gstin=v.gstin, state_code=v.state_code)


def find_vendor(session: Session, gstin: str | None, name: str | None) -> VendorData | None:
    """By exact GSTIN first, then by case-insensitive exact name."""
    if gstin and gstin.strip():
        found = session.scalar(select(Vendor).where(Vendor.gstin == gstin.strip()))
        if found is not None:
            return _vendor_data(found)
    if name and name.strip():
        wanted = " ".join(name.split()).casefold()
        found = session.scalar(select(Vendor).where(func.lower(Vendor.name) == wanted))
        if found is not None:
            return _vendor_data(found)
    return None


def get_purchase_order(session: Session, po_number: str | None) -> PurchaseOrderData | None:
    if not po_number or not po_number.strip():
        return None
    po = session.scalar(
        select(PurchaseOrder).where(
            func.upper(PurchaseOrder.po_number) == po_number.strip().upper()
        )
    )
    if po is None:
        return None
    lines = session.scalars(select(POLine).where(POLine.po_id == po.id).order_by(POLine.id)).all()
    return PurchaseOrderData(
        po_number=po.po_number,
        vendor_id=po.vendor_id,
        lines=tuple(
            POLineData(ln.id, ln.sku, ln.description, ln.qty, ln.unit_price, ln.tax_rate)
            for ln in lines
        ),
    )


def get_received_quantities(session: Session, po_number: str) -> dict[int, Decimal]:
    """Total received per PO line, summed over every goods receipt of the PO."""
    rows = session.execute(
        select(GRNLine.po_line_id, func.sum(GRNLine.qty_received))
        .join(GoodsReceipt, GoodsReceipt.id == GRNLine.grn_id)
        .join(PurchaseOrder, PurchaseOrder.id == GoodsReceipt.po_id)
        .where(func.upper(PurchaseOrder.po_number) == po_number.strip().upper())
        .group_by(GRNLine.po_line_id)
    ).all()
    return {int(line_id): total or _ZERO for line_id, total in rows}


def get_goods_receipts(session: Session, po_number: str) -> list[dict[str, object]]:
    """Each GRN of the PO with its lines, for the agent to read."""
    grns = session.execute(
        select(GoodsReceipt)
        .join(PurchaseOrder, PurchaseOrder.id == GoodsReceipt.po_id)
        .where(func.upper(PurchaseOrder.po_number) == po_number.strip().upper())
        .order_by(GoodsReceipt.received_at)
    ).scalars()
    out: list[dict[str, object]] = []
    for grn in grns:
        lines = session.scalars(select(GRNLine).where(GRNLine.grn_id == grn.id)).all()
        out.append(
            {
                "grn_number": grn.grn_number,
                "received_at": grn.received_at.isoformat(),
                "lines": [
                    {"po_line_id": ln.po_line_id, "qty_received": str(ln.qty_received)}
                    for ln in lines
                ],
            }
        )
    return out


def get_invoiced_quantities(
    session: Session, po_number: str, exclude_invoice_id: int | None = None
) -> dict[int, Decimal]:
    """Quantity per PO line already billed on other invoices that claim goods."""
    query = (
        select(InvoiceLine.po_line_id, func.sum(InvoiceLine.qty))
        .join(Invoice, Invoice.id == InvoiceLine.invoice_id)
        .where(
            func.upper(Invoice.po_number) == po_number.strip().upper(),
            InvoiceLine.po_line_id.is_not(None),
            Invoice.status.in_([str(s) for s in COUNTED_STATUSES]),
        )
        .group_by(InvoiceLine.po_line_id)
    )
    if exclude_invoice_id is not None:
        query = query.where(Invoice.id != exclude_invoice_id)
    return {
        int(line_id): total or _ZERO for line_id, total in session.execute(query).all() if line_id
    }


def get_prior_invoices(
    session: Session, vendor_id: int, exclude_invoice_id: int | None = None
) -> list[PriorInvoice]:
    query = select(Invoice).where(
        Invoice.vendor_id == vendor_id, Invoice.status != str(InvoiceStatus.PROCESSING)
    )
    if exclude_invoice_id is not None:
        query = query.where(Invoice.id != exclude_invoice_id)
    return [
        PriorInvoice(i.vendor_id or vendor_id, i.invoice_number, i.grand_total, i.invoice_date)
        for i in session.scalars(query)
    ]


def latest_extraction(session: Session, invoice_id: int) -> InvoiceExtraction | None:
    run = session.scalar(
        select(ExtractionRun)
        .where(ExtractionRun.invoice_id == invoice_id)
        .order_by(ExtractionRun.id.desc())
        .limit(1)
    )
    return None if run is None else InvoiceExtraction.model_validate(run.raw_output)


def to_invoice_data(extraction: InvoiceExtraction) -> InvoiceData:
    """Convert an extraction to the core type. Raises if a line is missing a number."""
    lines: list[InvoiceLineData] = []
    for n, ln in enumerate(extraction.lines, start=1):
        if ln.qty.value is None or ln.unit_price.value is None or ln.line_total.value is None:
            raise IncompleteInvoiceError(f"line {n} is missing a quantity, price or total")
        lines.append(
            InvoiceLineData(
                sku=ln.sku.value,
                description=ln.description.value or "",
                qty=ln.qty.value,
                unit_price=ln.unit_price.value,
                line_total=ln.line_total.value,
            )
        )
    return InvoiceData(
        vendor_gstin=extraction.vendor_gstin.value,
        invoice_number=extraction.invoice_number.value,
        invoice_date=extraction.invoice_date.value,
        po_number=extraction.po_number.value,
        lines=tuple(lines),
        subtotal=extraction.subtotal.value,
        cgst=extraction.cgst.value,
        sgst=extraction.sgst.value,
        igst=extraction.igst.value,
        tax_total=extraction.tax_total.value,
        grand_total=extraction.grand_total.value,
    )


def vendor_ids(session: Session) -> Sequence[int]:  # pragma: no cover - small helper for tests
    return session.scalars(select(Vendor.id)).all()
