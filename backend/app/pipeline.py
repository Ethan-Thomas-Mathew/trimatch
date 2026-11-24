"""The end-to-end invoice pipeline: upload -> extract -> agent -> rules -> policy -> record.

"The LLM reads, code decides." The model is used in three places (extraction, the agent's tool
choices, the explanation) and in none of them can it approve anything. The decision always comes
from ``app.core.policy`` applied to results of ``app.core.matching`` over inputs assembled from
the database. Any failure on the model side only ever adds a reason for human review.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.agent.explain import explain, fallback_explanation
from app.agent.inputs import assemble_match_inputs
from app.agent.loop import AgentTrace, run_agent
from app.agent.tools import ToolContext, read_only_session
from app.config import Settings
from app.core.matching import CheckResult, MatchInputs, run_three_way_match
from app.core.matching.matching import map_lines
from app.core.policy import (
    AGENT_STEP_LIMIT,
    EXTRACTION_FAILED,
    LLM_UNAVAILABLE,
    PolicyDecision,
    Reason,
    decide,
)
from app.db import queries
from app.db.models import DecisionRecord, Invoice, InvoiceLine, MatchResult
from app.db.session import get_sessionmaker
from app.extraction.pdf import PdfError
from app.extraction.persist import save_extraction_run
from app.extraction.service import ExtractionFailedError, ExtractionResult, extract_invoice
from app.llm.base import LLMError, LLMProvider
from app.matching_config import match_config, policy_config
from app.schemas.enums import DecidedBy, Decision, InvoiceStatus
from app.tracing import AuditTracer, audit
from app.uploads import resolve_upload, safe_display_name, save_upload, validate_pdf

log = logging.getLogger("trimatch.pipeline")

EXTRACTION_INCOMPLETE = "extraction_incomplete"
_STATUS_FOR = {
    Decision.AUTO_APPROVE: InvoiceStatus.AUTO_APPROVED,
    Decision.NEEDS_REVIEW: InvoiceStatus.NEEDS_REVIEW,
    Decision.REJECT: InvoiceStatus.REJECTED,
}


def create_invoice(
    session: Session, content: bytes, original_name: str | None, settings: Settings
) -> Invoice:
    """Validate and store an upload and create its PROCESSING row. Raises ``UploadError``."""
    validate_pdf(content, settings.max_upload_mb)
    stored, digest = save_upload(content, settings.upload_dir)
    invoice = Invoice(status=str(InvoiceStatus.PROCESSING), file_path=stored)
    session.add(invoice)
    session.flush()
    audit(
        session,
        "invoice",
        invoice.id,
        "uploaded",
        "reviewer",
        {
            "original_name": safe_display_name(original_name),
            "bytes": len(content),
            "sha256": digest,
        },
    )
    return invoice


def _clip(value: str | None, size: int) -> str | None:
    return None if value is None else value[:size]


def _apply_extraction(session: Session, invoice: Invoice, result: ExtractionResult) -> None:
    ex = result.extraction
    vendor = queries.find_vendor(session, ex.vendor_gstin.value, ex.vendor_name.value)
    invoice.vendor_id = vendor.id if vendor else None
    invoice.invoice_number = _clip(ex.invoice_number.value, 60)
    invoice.invoice_date = ex.invoice_date.value
    invoice.po_number = _clip(ex.po_number.value, 40)
    invoice.subtotal = ex.subtotal.value
    invoice.tax_total = ex.tax_total.value
    invoice.grand_total = ex.grand_total.value

    session.execute(delete(InvoiceLine).where(InvoiceLine.invoice_id == invoice.id))
    try:
        queries.to_invoice_data(ex)  # raises if any line lacks a number
    except queries.IncompleteInvoiceError:
        return  # header is stored; the pipeline will route the invoice to a human
    for ln in ex.lines:
        session.add(
            InvoiceLine(
                invoice_id=invoice.id,
                sku=_clip(ln.sku.value, 40),
                description=(ln.description.value or "")[:300],
                qty=ln.qty.value,
                unit_price=ln.unit_price.value,
                line_total=ln.line_total.value,
            )
        )


def _link_lines_to_po(session: Session, invoice_id: int, inputs: MatchInputs) -> None:
    """Record which PO line each invoice line billed, so later invoices see it as 'already
    invoiced'. Only done against a PO that exists and belongs to the invoicing vendor."""
    if inputs.po is None or inputs.vendor is None or inputs.po.vendor_id != inputs.vendor.id:
        return
    rows = session.scalars(
        select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id).order_by(InvoiceLine.id)
    ).all()
    for row, (_, po_line) in zip(rows, map_lines(inputs.invoice.lines, inputs.po), strict=False):
        row.po_line_id = po_line.id if po_line else None


def _summary(result: ExtractionResult) -> dict[str, Any]:
    """What the agent is shown about the invoice (all of it is untrusted document content)."""
    ex = result.extraction
    return {
        "vendor_name": ex.vendor_name.value,
        "vendor_gstin": ex.vendor_gstin.value,
        "invoice_number": ex.invoice_number.value,
        "invoice_date": ex.invoice_date.value,
        "po_number": ex.po_number.value,
        "grand_total": ex.grand_total.value,
        "line_count": len(ex.lines),
    }


def process_invoice(invoice_id: int, provider: LLMProvider, settings: Settings) -> Decision | None:
    """Run the whole pipeline for a stored invoice. Never raises: unexpected errors mark the
    invoice FAILED (so a human sees it) and are logged."""
    started = time.perf_counter()
    try:
        return _process(invoice_id, provider, settings, started)
    except Exception:
        log.exception("pipeline failed", extra={"invoice_id": invoice_id})
        with get_sessionmaker()() as session, session.begin():
            invoice = session.get(Invoice, invoice_id)
            if invoice is not None:
                invoice.status = str(InvoiceStatus.FAILED)
                audit(session, "invoice", invoice_id, "pipeline_error", "system", {})
        return None


def _process(
    invoice_id: int, provider: LLMProvider, settings: Settings, started: float
) -> Decision:
    sessions = get_sessionmaker()
    with sessions() as session:
        invoice = session.get(Invoice, invoice_id)
        if invoice is None or invoice.file_path is None:
            raise LookupError(f"invoice {invoice_id} has no stored file")
        stored_name = invoice.file_path
    pdf_bytes = resolve_upload(settings.upload_dir, stored_name).read_bytes()

    # 1. Extract (LLM). Failure only ever adds a reason for human review.
    forced: list[Reason] = []
    result: ExtractionResult | None = None
    try:
        result = extract_invoice(pdf_bytes, provider, settings)
    except LLMError as exc:
        forced.append(Reason(LLM_UNAVAILABLE, f"The language model was unavailable: {exc}"))
    except (ExtractionFailedError, PdfError) as exc:
        forced.append(Reason(EXTRACTION_FAILED, f"The invoice could not be read: {exc}"))

    with sessions() as session, session.begin():
        invoice = session.get(Invoice, invoice_id)
        assert invoice is not None
        if result is not None:
            save_extraction_run(session, invoice_id, result)
            _apply_extraction(session, invoice, result)
            audit(
                session, "invoice", invoice_id, "extraction", "extractor",
                {
                    "mode": result.mode, "model": result.model, "attempts": result.attempts,
                    "tokens_in": result.tokens_in, "tokens_out": result.tokens_out,
                    "latency_ms": result.latency_ms,
                    "min_confidence": result.extraction.min_confidence(),
                },
            )  # fmt: skip
        else:
            audit(session, "invoice", invoice_id, "extraction_failed", "extractor",
                  {"reasons": [r.message for r in forced]})  # fmt: skip

    # 2. Agent loop with read-only tools (LLM chooses tools; it cannot decide anything).
    trace: AgentTrace | None = None
    if result is not None:
        with sessions() as write_session:
            tracer = AuditTracer(write_session, invoice_id)
            with read_only_session() as ro:
                trace = run_agent(
                    provider,
                    settings.ollama_text_model,
                    ToolContext(ro, settings, invoice_id),
                    _summary(result),
                    settings.agent_max_tool_calls,
                    tracer,
                )
            write_session.commit()
        if trace.outcome == "step_limit":
            forced.append(
                Reason(AGENT_STEP_LIMIT, "The agent used its maximum number of tool calls.")
            )
        elif trace.outcome == "llm_error":
            forced.append(
                Reason(LLM_UNAVAILABLE, f"The language model was unavailable: {trace.error}")
            )

    # 3. Deterministic checks and policy. Inputs come from the database, not from the model.
    with sessions() as session, session.begin():
        invoice = session.get(Invoice, invoice_id)
        assert invoice is not None
        results: list[CheckResult] = []
        inputs: MatchInputs | None = None
        try:
            inputs = assemble_match_inputs(session, invoice_id, settings)
            results = run_three_way_match(inputs, match_config(settings))
        except queries.IncompleteInvoiceError as exc:
            if result is not None:
                forced.append(Reason(EXTRACTION_INCOMPLETE, f"The extraction is incomplete: {exc}"))

        if inputs is not None:
            _link_lines_to_po(session, invoice_id, inputs)
        session.execute(delete(MatchResult).where(MatchResult.invoice_id == invoice_id))
        for r in results:
            session.add(
                MatchResult(
                    invoice_id=invoice_id,
                    check_name=str(r.name),
                    passed=r.passed,
                    expected=r.expected,
                    actual=r.actual,
                    message=r.message,
                )
            )

        grand_total: Decimal | None = result.extraction.grand_total.value if result else None
        confidence = result.extraction.min_confidence() if result else None
        decision: PolicyDecision = decide(
            results, grand_total, confidence, policy_config(settings), forced
        )

        # 4. Explanation: model if it is reachable, otherwise deterministic text.
        model_down = any(r.code == LLM_UNAVAILABLE for r in decision.reasons)
        if model_down:
            explanation, used_model = fallback_explanation(decision), False
        else:
            explanation, used_model = explain(
                provider, settings.ollama_text_model, decision, results
            )

        session.add(
            DecisionRecord(
                invoice_id=invoice_id,
                decision=str(decision.decision),
                decided_by=str(DecidedBy.POLICY),
                reasons=[{"code": r.code, "message": r.message} for r in decision.reasons],
                explanation=explanation,
            )
        )
        invoice.status = str(_STATUS_FOR[decision.decision])
        audit(
            session, "invoice", invoice_id, "decision", "policy",
            {
                "decision": str(decision.decision),
                "reasons": [r.code for r in decision.reasons],
                "explanation_from_model": used_model,
                "total_ms": int((time.perf_counter() - started) * 1000),
                "agent_tools": trace.tools_called if trace else [],
            },
        )  # fmt: skip
    return decision.decision
