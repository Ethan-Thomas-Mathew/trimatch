"""The accuracy CLI end to end, with a fake model instead of a real one."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.llm.base import LLMError, LLMRequest, LLMResponse
from generate_dataset import generate
from run_extraction import run, summarise
from world import DEFAULT_SEED, build_world

from .test_extraction import oracle_json


class OracleSequence:
    """Answers perfectly, in dataset order (the CLI processes invoices in sorted id order)."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = texts

    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text=self.texts.pop(0), model=request.model, latency_ms=5)


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("ds")
    generate(build_world(DEFAULT_SEED), out)
    return out


def test_cli_reports_perfect_accuracy_for_a_perfect_model(dataset: Path, tmp_path: Path) -> None:
    world = build_world(DEFAULT_SEED)
    chosen = world.invoices[:12]
    assert any(i.scanned for i in world.invoices)
    scanned_ids = [i.invoice_id for i in world.invoices if i.scanned][:2]
    ids = sorted({*(i.invoice_id for i in chosen), *scanned_ids})
    by_id = {i.invoice_id: i for i in world.invoices}
    provider = OracleSequence([json.dumps(oracle_json(by_id[i])) for i in ids])

    summary = run(dataset, tmp_path, provider, ids, None)

    assert summary["failed"] == 0
    assert summary["field_accuracy_digital"] == 1.0
    assert summary["field_accuracy_scanned"] == 1.0
    assert summary["retried_after_invalid_output"] == 0
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / f"{ids[0]}.json").exists()


def test_cli_counts_failures_instead_of_crashing(dataset: Path, tmp_path: Path) -> None:
    class Down:
        def generate(self, request: LLMRequest) -> LLMResponse:
            raise LLMError("connection refused")

    summary = run(dataset, tmp_path, Down(), ["TM-0001", "TM-0002"], None)
    assert summary["failed"] == 2
    assert summary["extracted"] == 0
    assert summary["field_accuracy_digital"] is None  # no number is better than a fake one


def test_summary_splits_digital_and_scanned() -> None:
    rows = [
        {"status": "ok", "scanned": False, "correct": 9, "total": 10, "attempts": 1,
         "latency_ms": 10},
        {"status": "ok", "scanned": True, "correct": 5, "total": 10, "attempts": 2,
         "latency_ms": 30},
        {"status": "failed", "scanned": False},
    ]  # fmt: skip
    s = summarise(rows)
    assert s["field_accuracy_digital"] == 0.9
    assert s["field_accuracy_scanned"] == 0.5
    assert (s["extracted"], s["failed"], s["retried_after_invalid_output"]) == (2, 1, 1)
