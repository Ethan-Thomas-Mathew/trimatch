"""HTTP endpoints (build plan Phase 6). Every route here requires the reviewer login."""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import Numeric, cast, func, select
from sqlalchemy.orm import Session

from app.api.deps import ProviderDep, ReviewerDep, SessionDep, SettingsDep, require_reviewer
from app.api.money import cost_in_inr
from app.api.schemas import (
    CheckOut,
    DecisionOut,
    ExtractionOut,
    InvoiceCreated,
    InvoiceDetail,
    InvoiceList,
    InvoiceSummary,
    LineOut,
    Metrics,
    ReviewActionOut,
    ReviewRequest,
    TraceEvent,
)
from app.db.models import (
    AuditLog,
    DecisionRecord,
    ExtractionRun,
    Invoice,
    InvoiceLine,
    MatchResult,
    ReviewAction,
    Vendor,
)
from app.pipeline import create_invoice, process_invoice
from app.schemas.enums import HUMAN_APPROVE, DecidedBy, InvoiceStatus
from app.tracing import audit
from app.uploads import UploadError, resolve_upload

router = APIRouter(dependencies=[Depends(require_reviewer)])

# A human may review these. A policy REJECT is final; an approved invoice is already done.
REVIEWABLE = {str(InvoiceStatus.NEEDS_REVIEW), str(InvoiceStatus.FAILED)}


def _latest_decision(session: Session, invoice_id: int) -> DecisionRecord | None:
    return session.scalars(
        select(DecisionRecord)
        .where(DecisionRecord.invoice_id == invoice_id)
        .order_by(DecisionRecord.id.desc())
        .limit(1)
    ).first()


def _get_invoice(session: Session, invoice_id: int) -> Invoice:
    invoice = session.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(404, "Invoice not found.")
    return invoice


# ---------------------------------------------------------------------------- upload
@router.post("/invoices", status_code=202, response_model=InvoiceCreated)
def upload_invoice(
    background: BackgroundTasks,
    session: SessionDep,
    reviewer: ReviewerDep,
    settings: SettingsDep,
    provider: ProviderDep,
    file: Annotated[UploadFile, File(description="A PDF invoice")],
) -> InvoiceCreated:
    """Store the PDF and return immediately; extraction and matching continue in the background."""
    content = file.file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    try:
        invoice = create_invoice(session, content, file.filename, settings)
    except UploadError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    session.commit()  # the row must be visible before the background task reads it
    background.add_task(process_invoice, invoice.id, provider, settings)
    return InvoiceCreated(id=invoice.id, status=invoice.status)


# ------------------------------------------------------------------------------ list
@router.get("/invoices", response_model=InvoiceList)
def list_invoices(
    session: SessionDep,
    status: Annotated[str | None, Query(description="Filter by status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InvoiceList:
    if status is not None and status not in {str(s) for s in InvoiceStatus}:
        raise HTTPException(422, f"Unknown status {status!r}.")
    base = select(Invoice)
    count = select(func.count()).select_from(Invoice)
    if status:
        base = base.where(Invoice.status == status)
        count = count.where(Invoice.status == status)
    invoices = session.scalars(base.order_by(Invoice.id.desc()).limit(limit).offset(offset)).all()

    vendors = {
        v.id: v.name
        for v in session.scalars(
            select(Vendor).where(Vendor.id.in_({i.vendor_id for i in invoices if i.vendor_id}))
        )
    }
    decisions: dict[int, DecisionRecord] = {}
    for d in session.scalars(
        select(DecisionRecord)
        .where(DecisionRecord.invoice_id.in_([i.id for i in invoices]))
        .order_by(DecisionRecord.id)
    ):
        decisions[d.invoice_id] = d  # later rows overwrite earlier ones
    items = [
        InvoiceSummary(
            id=i.id,
            status=i.status,
            vendor_name=vendors.get(i.vendor_id) if i.vendor_id else None,
            invoice_number=i.invoice_number,
            invoice_date=i.invoice_date,
            grand_total=i.grand_total,
            decision=decisions[i.id].decision if i.id in decisions else None,
            decided_by=decisions[i.id].decided_by if i.id in decisions else None,
        )
        for i in invoices
    ]
    return InvoiceList(items=items, total=session.scalar(count) or 0)


# ---------------------------------------------------------------------------- detail
def _trace_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep traces small: a tool result becomes a short preview."""
    out = dict(payload)
    if "result" in out:
        out["result_preview"] = json.dumps(out.pop("result"), ensure_ascii=False)[:400]
    return out


def build_detail(session: Session, invoice: Invoice) -> InvoiceDetail:
    vendor = session.get(Vendor, invoice.vendor_id) if invoice.vendor_id else None
    lines = session.scalars(
        select(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id).order_by(InvoiceLine.id)
    ).all()
    run = session.scalars(
        select(ExtractionRun)
        .where(ExtractionRun.invoice_id == invoice.id)
        .order_by(ExtractionRun.id.desc())
        .limit(1)
    ).first()
    confidences = [float(v) for v in run.field_confidences.values()] if run else []
    checks = session.scalars(
        select(MatchResult).where(MatchResult.invoice_id == invoice.id).order_by(MatchResult.id)
    ).all()
    decision = _latest_decision(session, invoice.id)
    reviews = session.scalars(
        select(ReviewAction).where(ReviewAction.invoice_id == invoice.id).order_by(ReviewAction.id)
    ).all()
    events = session.scalars(
        select(AuditLog)
        .where(AuditLog.entity == "invoice", AuditLog.entity_id == invoice.id)
        .order_by(AuditLog.id)
    ).all()
    return InvoiceDetail(
        id=invoice.id,
        status=invoice.status,
        has_file=invoice.file_path is not None,
        vendor_name=vendor.name if vendor else None,
        invoice_number=invoice.invoice_number,
        invoice_date=invoice.invoice_date,
        po_number=invoice.po_number,
        subtotal=invoice.subtotal,
        tax_total=invoice.tax_total,
        grand_total=invoice.grand_total,
        lines=[
            LineOut(
                sku=ln.sku,
                description=ln.description,
                qty=ln.qty,
                unit_price=ln.unit_price,
                line_total=ln.line_total,
            )
            for ln in lines
        ],
        extraction=(
            ExtractionOut(
                model=run.model,
                fields=run.raw_output,
                min_confidence=min(confidences) if confidences else None,
                tokens_in=run.tokens_in,
                tokens_out=run.tokens_out,
                latency_ms=run.latency_ms,
            )
            if run
            else None
        ),
        checks=[
            CheckOut(
                check=c.check_name,
                passed=c.passed,
                expected=c.expected,
                actual=c.actual,
                message=c.message,
            )
            for c in checks
        ],
        decision=(
            DecisionOut(
                decision=decision.decision,
                decided_by=decision.decided_by,
                reasons=list(decision.reasons),
                explanation=decision.explanation,
            )
            if decision
            else None
        ),
        reviews=[
            ReviewActionOut(
                reviewer=r.reviewer, action=r.action, comment=r.comment, created_at=r.created_at
            )
            for r in reviews
        ],
        trace=[
            TraceEvent(
                action=e.action,
                actor=e.actor,
                created_at=e.created_at,
                payload=_trace_payload(e.payload),
            )
            for e in events
        ],
    )


@router.get("/invoices/{invoice_id}", response_model=InvoiceDetail)
def get_invoice(invoice_id: int, session: SessionDep) -> InvoiceDetail:
    return build_detail(session, _get_invoice(session, invoice_id))


@router.get("/invoices/{invoice_id}/file")
def get_invoice_file(invoice_id: int, session: SessionDep, settings: SettingsDep) -> FileResponse:
    invoice = _get_invoice(session, invoice_id)
    if invoice.file_path is None:
        raise HTTPException(404, "This invoice has no stored file.")
    try:
        path = resolve_upload(settings.upload_dir, invoice.file_path)
    except UploadError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


# ---------------------------------------------------------------------------- review
@router.post("/invoices/{invoice_id}/review", response_model=InvoiceDetail)
def review_invoice(
    invoice_id: int, body: ReviewRequest, session: SessionDep, reviewer: ReviewerDep
) -> InvoiceDetail:
    """A human approves or rejects an invoice. A comment is required.

    - NEEDS_REVIEW / FAILED: approve or reject.
    - REJECTED: approve later (reverse the rejection). If the RULES ENGINE rejected it (a
      duplicate or malformed GSTIN), the request must also carry ``acknowledge_override=true``.
    - Anything else (already approved, auto-approved, still processing): 409.
    """
    invoice = session.execute(
        select(Invoice).where(Invoice.id == invoice_id).with_for_update()  # no double reviews
    ).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(404, "Invoice not found.")

    approved = body.action == "approve"
    previous_status = invoice.status
    override_reasons: list[str] = []
    is_reversal = previous_status == str(InvoiceStatus.REJECTED)

    if previous_status in REVIEWABLE:
        pass
    elif is_reversal and approved:
        latest = _latest_decision(session, invoice.id)
        if latest is not None and latest.decided_by == str(DecidedBy.POLICY):
            # The rules engine rejected this: a duplicate or a malformed GSTIN. Approving it can
            # mean paying twice or paying a non-existent supplier, so it needs a deliberate yes.
            override_reasons = [str(r.get("code", "")) for r in latest.reasons]
            if not body.acknowledge_override:
                raise HTTPException(
                    409,
                    "The rules engine rejected this invoice. To approve it anyway, confirm that "
                    "you understand the reasons (acknowledge_override).",
                )
    else:
        hint = (
            " A rejected invoice can only be approved, not rejected again."
            if is_reversal
            else " Only invoices that need review, or rejected ones, can be reviewed."
        )
        raise HTTPException(409, f"This invoice is {invoice.status}.{hint}")

    action_name = "approve_override" if (is_reversal and approved) else body.action
    session.add(
        ReviewAction(
            invoice_id=invoice.id, reviewer=reviewer, action=action_name, comment=body.comment
        )
    )
    session.add(
        DecisionRecord(
            invoice_id=invoice.id,
            decision=HUMAN_APPROVE if approved else "REJECT",
            decided_by=str(DecidedBy.HUMAN),
            reasons=[{"code": "human_review", "message": body.comment}],
            explanation=body.comment,
        )
    )
    invoice.status = str(InvoiceStatus.APPROVED if approved else InvoiceStatus.REJECTED)
    audit(
        session, "invoice", invoice.id, f"review_{action_name}", reviewer,
        {
            "comment": body.comment,
            "previous_status": previous_status,
            "new_status": invoice.status,
            "reversal": is_reversal,
            "overrode_policy_reasons": override_reasons,
        },
    )  # fmt: skip
    session.flush()
    return build_detail(session, invoice)


# --------------------------------------------------------------------------- metrics
@router.get("/metrics", response_model=Metrics)
def get_metrics(session: SessionDep, settings: SettingsDep) -> Metrics:
    # Explicit rows and typed dicts: dict() on the raw Result would treat it as a mapping.
    status_rows = session.execute(select(Invoice.status, func.count()).group_by(Invoice.status))
    by_status: dict[str, int] = {str(name): int(n) for name, n in status_rows.all()}
    latest = select(func.max(DecisionRecord.id)).group_by(DecisionRecord.invoice_id)
    decision_rows = session.execute(
        select(DecisionRecord.decision, func.count())
        .where(DecisionRecord.id.in_(latest))
        .group_by(DecisionRecord.decision)
    )
    by_decision: dict[str, int] = {str(name): int(n) for name, n in decision_rows.all()}
    runs = session.execute(
        select(
            func.coalesce(func.sum(ExtractionRun.cost_usd), 0),
            func.count(func.distinct(ExtractionRun.invoice_id)),
            func.coalesce(func.avg(ExtractionRun.latency_ms), 0),
        )
    ).one()
    avg_ms = session.scalar(
        select(
            func.coalesce(func.avg(cast(AuditLog.payload["total_ms"].astext, Numeric)), 0)
        ).where(AuditLog.action == "decision", AuditLog.payload.has_key("total_ms"))
    )
    avg_usd = float(runs[0] / runs[1]) if runs[1] else 0.0
    avg_inr = cost_in_inr(avg_usd, settings.usd_to_inr)
    return Metrics(
        invoices=sum(by_status.values()),
        by_status=by_status,
        by_decision=by_decision,
        awaiting_review=by_status.get("NEEDS_REVIEW", 0) + by_status.get("FAILED", 0),
        avg_cost_usd=avg_usd,
        avg_cost_inr=avg_inr,
        avg_extraction_latency_ms=float(runs[2]),
        avg_pipeline_seconds=float(avg_ms or 0) / 1000,
    )
