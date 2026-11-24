"""Request and response models for the API. Money is serialised as exact decimal strings."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class InvoiceCreated(BaseModel):
    id: int
    status: str


class InvoiceSummary(BaseModel):
    id: int
    status: str
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: dt.date | None
    grand_total: Decimal | None
    decision: str | None
    decided_by: str | None


class InvoiceList(BaseModel):
    items: list[InvoiceSummary]
    total: int


class LineOut(BaseModel):
    sku: str | None
    description: str
    qty: Decimal
    unit_price: Decimal
    line_total: Decimal


class CheckOut(BaseModel):
    check: str
    passed: bool
    expected: str | None
    actual: str | None
    message: str


class DecisionOut(BaseModel):
    decision: str
    decided_by: str
    reasons: list[dict[str, Any]]
    explanation: str | None


class ReviewActionOut(BaseModel):
    reviewer: str
    action: str
    comment: str
    created_at: dt.datetime


class TraceEvent(BaseModel):
    action: str
    actor: str
    created_at: dt.datetime
    payload: dict[str, Any]


class ExtractionOut(BaseModel):
    model: str
    # The extractor's JSON: {"vendor_name": {"value": ..., "confidence": ...}, ...}
    fields: dict[str, Any]
    min_confidence: float | None
    tokens_in: int
    tokens_out: int
    latency_ms: int


class InvoiceDetail(BaseModel):
    id: int
    status: str
    has_file: bool
    vendor_name: str | None
    invoice_number: str | None
    invoice_date: dt.date | None
    po_number: str | None
    subtotal: Decimal | None
    tax_total: Decimal | None
    grand_total: Decimal | None
    lines: list[LineOut]
    extraction: ExtractionOut | None
    checks: list[CheckOut]
    decision: DecisionOut | None
    reviews: list[ReviewActionOut]
    trace: list[TraceEvent]


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "reject"]
    comment: str = Field(max_length=2000)

    @field_validator("comment")
    @classmethod
    def _comment_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a comment is required")
        return value


class Metrics(BaseModel):
    invoices: int
    by_status: dict[str, int]
    by_decision: dict[str, int]
    awaiting_review: int
    avg_cost_usd: float
    avg_extraction_latency_ms: float
    avg_pipeline_seconds: float
