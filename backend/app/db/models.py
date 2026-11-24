"""SQLAlchemy ORM models for every table in build plan section 6.

Conventions:
- Money is ``Numeric(14, 2)`` (exact decimal, paise precision). Never float.
- Quantities are ``Numeric(12, 3)`` so fractional units (kg, litres) are possible.
- Timestamps are timezone-aware.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.schemas.enums import HUMAN_APPROVE, DecidedBy, Decision

# A naming convention makes Alembic-generated constraint names stable and readable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Money = Numeric(14, 2)
Qty = Numeric(12, 3)


def _sql_in(values: list[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {dict[str, Any]: JSONB, list[Any]: JSONB}


class Vendor(Base):
    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    gstin: Mapped[str] = mapped_column(String(15), unique=True)
    state_code: Mapped[str] = mapped_column(String(2))


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(40), unique=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20))

    vendor: Mapped[Vendor] = relationship()
    lines: Mapped[list[POLine]] = relationship(back_populates="po", order_by="POLine.id")
    receipts: Mapped[list[GoodsReceipt]] = relationship(back_populates="po")


class POLine(Base):
    __tablename__ = "po_lines"
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        CheckConstraint("unit_price >= 0", name="unit_price_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    sku: Mapped[str] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(300))
    qty: Mapped[Decimal] = mapped_column(Qty)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))  # percent, e.g. 18.00

    po: Mapped[PurchaseOrder] = relationship(back_populates="lines")


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    grn_number: Mapped[str] = mapped_column(String(40), unique=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    received_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))

    po: Mapped[PurchaseOrder] = relationship(back_populates="receipts")
    lines: Mapped[list[GRNLine]] = relationship(back_populates="grn", order_by="GRNLine.id")


class GRNLine(Base):
    __tablename__ = "grn_lines"
    __table_args__ = (CheckConstraint("qty_received >= 0", name="qty_received_non_negative"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    grn_id: Mapped[int] = mapped_column(ForeignKey("goods_receipts.id"), index=True)
    po_line_id: Mapped[int] = mapped_column(ForeignKey("po_lines.id"), index=True)
    qty_received: Mapped[Decimal] = mapped_column(Qty)

    grn: Mapped[GoodsReceipt] = relationship(back_populates="lines")


class Invoice(Base):
    __tablename__ = "invoices"
    # Not unique: exact duplicates must be stored so they can be rejected and audited.
    __table_args__ = (Index("ix_invoices_vendor_invoice_number", "vendor_id", "invoice_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable: the vendor may not be identifiable until extraction and lookup succeed.
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), index=True)
    invoice_number: Mapped[str | None] = mapped_column(String(60))
    invoice_date: Mapped[dt.date | None] = mapped_column(Date)
    po_number: Mapped[str | None] = mapped_column(String(40), index=True)
    subtotal: Mapped[Decimal | None] = mapped_column(Money)
    tax_total: Mapped[Decimal | None] = mapped_column(Money)
    grand_total: Mapped[Decimal | None] = mapped_column(Money)
    file_path: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), index=True)

    vendor: Mapped[Vendor | None] = relationship()
    lines: Mapped[list[InvoiceLine]] = relationship(
        back_populates="invoice", order_by="InvoiceLine.id"
    )


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    po_line_id: Mapped[int | None] = mapped_column(ForeignKey("po_lines.id"), index=True)
    # Not in the original §6 table; see docs/DECISIONS.md D-007.
    sku: Mapped[str | None] = mapped_column(String(40))
    description: Mapped[str] = mapped_column(String(300))
    qty: Mapped[Decimal] = mapped_column(Qty)
    unit_price: Mapped[Decimal] = mapped_column(Money)
    line_total: Mapped[Decimal] = mapped_column(Money)

    invoice: Mapped[Invoice] = relationship(back_populates="lines")


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    model: Mapped[str] = mapped_column(String(100))
    raw_output: Mapped[dict[str, Any]]
    field_confidences: Mapped[dict[str, Any]]
    tokens_in: Mapped[int] = mapped_column(Integer)
    tokens_out: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    check_name: Mapped[str] = mapped_column(String(50))
    passed: Mapped[bool]
    expected: Mapped[str | None] = mapped_column(Text)
    actual: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class DecisionRecord(Base):
    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint(
            f"decision IN ({_sql_in([*(d.value for d in Decision), HUMAN_APPROVE])})",
            name="decision",
        ),
        CheckConstraint(
            f"decided_by IN ({_sql_in([d.value for d in DecidedBy])})", name="decided_by"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    decision: Mapped[str] = mapped_column(String(20))
    decided_by: Mapped[str] = mapped_column(String(10))
    reasons: Mapped[list[Any]]
    explanation: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ReviewAction(Base):
    __tablename__ = "review_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(20))
    comment: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_entity", "entity", "entity_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(50))
    actor: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict[str, Any]]
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
