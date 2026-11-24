"""Saving an extraction run to Postgres (needs the database from `docker compose up`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import ExtractionRun, Invoice
from app.extraction.persist import save_extraction_run
from app.extraction.service import extract_invoice
from app.llm.base import LLMRequest, LLMResponse
from render import render_invoice
from tests.unit.test_extraction import oracle_json
from world import DEFAULT_SEED, build_world

pytestmark = pytest.mark.integration


def test_extraction_run_is_saved_with_usage_and_confidences(
    migrated_engine: Engine, tmp_path: Path
) -> None:
    world = build_world(DEFAULT_SEED)
    inv = next(i for i in world.invoices if not i.scanned)
    render_invoice(inv, world.buyer, tmp_path / "x.pdf")

    class OneShot:
        def generate(self, request: LLMRequest) -> LLMResponse:
            return LLMResponse(
                text=json.dumps(oracle_json(inv)),
                model=request.model,
                tokens_in=100,
                tokens_out=40,
                latency_ms=250,
            )

    result = extract_invoice((tmp_path / "x.pdf").read_bytes(), OneShot(), Settings(_env_file=None))

    with Session(migrated_engine) as s:
        invoice = Invoice(status="PROCESSING")
        s.add(invoice)
        s.flush()
        run = save_extraction_run(s, invoice.id, result)
        s.commit()

        stored = s.get(ExtractionRun, run.id)
        assert stored is not None
        assert (stored.tokens_in, stored.tokens_out, stored.latency_ms) == (100, 40, 250)
        assert stored.cost_usd == 0
        assert stored.field_confidences["invoice_number"] == 1.0
        assert stored.raw_output["invoice_number"]["value"] == inv.invoice_number
