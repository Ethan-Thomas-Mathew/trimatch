"""Store an extraction run (tokens, cost, latency, confidences) in ``extraction_runs``."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.models import ExtractionRun
from app.extraction.service import ExtractionResult


def save_extraction_run(
    session: Session, invoice_id: int, result: ExtractionResult
) -> ExtractionRun:
    run = ExtractionRun(
        invoice_id=invoice_id,
        model=result.model,
        raw_output=result.raw_output,
        field_confidences=result.extraction.confidences(),
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
    )
    session.add(run)
    session.flush()
    return run
