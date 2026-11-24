"""The full pipeline and agent against a real Postgres, with a simulated model.

The simulated model (``evals/oracle.py``) is NOT a language model. These tests prove the
plumbing (PDF -> extraction -> agent tools -> rules -> policy -> database) and the safety
properties, not extraction quality.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.agent.loop import run_agent
from app.agent.tools import TOOLS, ToolContext, ToolError, execute_tool, read_only_session
from app.config import get_settings
from app.db.models import AuditLog, DecisionRecord, Invoice, MatchResult, Vendor
from app.db.session import get_sessionmaker
from app.llm.base import LLMError, LLMRequest, LLMResponse
from app.pipeline import create_invoice, process_invoice
from app.schemas.enums import CheckName, InvoiceStatus
from oracle import OracleProvider
from tests.integration.conftest import Env, forget

pytestmark = pytest.mark.integration


def run_one(env: Env, pdf: bytes, provider: Any) -> int:
    with get_sessionmaker()() as s, s.begin():
        invoice = create_invoice(s, pdf, "test.pdf", env.settings)
        invoice_id = invoice.id
    process_invoice(invoice_id, provider, env.settings)
    return invoice_id


def final(env: Env, invoice_id: int) -> tuple[Invoice, DecisionRecord]:
    with Session(env.engine) as s:
        inv = s.get(Invoice, invoice_id)
        d = s.scalars(
            select(DecisionRecord)
            .where(DecisionRecord.invoice_id == invoice_id)
            .order_by(DecisionRecord.id.desc())
        ).first()
        assert inv is not None
        assert d is not None
        return inv, d


def clean_pdf(env: Env) -> tuple[bytes, dict[str, Any]]:
    for path in sorted((env.dataset / "ground_truth").glob("*.json")):
        truth = json.loads(path.read_text("utf-8"))
        if (
            truth["scenario"] == "clean"
            and not truth["scanned"]
            and not truth["over_auto_approve_limit"]
        ):
            # Earlier copies are cleared so the pipeline does not see it as a duplicate.
            forget(env, truth["fields"]["invoice_number"])
            return (env.dataset / truth["pdf"]).read_bytes(), truth
    raise AssertionError("no clean invoice")


@dataclass
class Outcome:
    """Everything the all-100 tests need, captured at run time (later tests may purge rows)."""

    truth: dict[str, Any]
    invoice_id: int
    status: str
    decision: str
    decided_by: str
    reasons: list[dict[str, Any]]
    explanation: str
    checks: set[str]
    actions: list[str]
    steps: list[dict[str, Any]]


# --------------------------------------------------------- all 100 end to end
@pytest.fixture(scope="module")
def all_outcomes(env: Env) -> list[Outcome]:
    provider = OracleProvider(env.dataset)
    rows: list[Outcome] = []
    for path in sorted((env.dataset / "ground_truth").glob("*.json")):
        truth = json.loads(path.read_text("utf-8"))
        invoice_id = run_one(env, (env.dataset / truth["pdf"]).read_bytes(), provider)
        inv, d = final(env, invoice_id)
        with Session(env.engine) as s:
            checks = set(
                s.scalars(
                    select(MatchResult.check_name).where(MatchResult.invoice_id == invoice_id)
                )
            )
            events = s.scalars(
                select(AuditLog).where(AuditLog.entity_id == invoice_id).order_by(AuditLog.id)
            ).all()
        rows.append(
            Outcome(
                truth=truth,
                invoice_id=invoice_id,
                status=inv.status,
                decision=d.decision,
                decided_by=d.decided_by,
                reasons=list(d.reasons),
                explanation=d.explanation or "",
                checks=checks,
                actions=[e.action for e in events],
                steps=[dict(e.payload) for e in events if e.action == "agent_step"],
            )
        )
    return rows


def test_every_dataset_invoice_ends_with_the_expected_decision(
    all_outcomes: list[Outcome],
) -> None:
    wrong = [
        f"{o.truth['invoice_id']} {o.truth['scenario']}: {o.decision}"
        for o in all_outcomes
        if o.decision != o.truth["expected_decision"]
    ]
    assert not wrong, "\n".join(wrong)


def test_false_approval_rate_is_zero_and_injections_are_not_approved(
    all_outcomes: list[Outcome],
) -> None:
    approved_wrongly = [
        o.truth["invoice_id"]
        for o in all_outcomes
        if o.status == InvoiceStatus.AUTO_APPROVED
        and o.truth["expected_decision"] != "AUTO_APPROVE"
    ]
    injected = [o.decision for o in all_outcomes if o.truth["injection"]]
    assert approved_wrongly == []
    assert len(injected) == 5
    assert "AUTO_APPROVE" not in injected


def test_statuses_follow_decisions(all_outcomes: list[Outcome]) -> None:
    expected = {
        "AUTO_APPROVE": "AUTO_APPROVED",
        "NEEDS_REVIEW": "NEEDS_REVIEW",
        "REJECT": "REJECTED",
    }
    for o in all_outcomes:
        assert o.status == expected[o.truth["expected_decision"]], o.truth["invoice_id"]


def test_every_non_approved_invoice_has_an_explanation_and_reasons(
    all_outcomes: list[Outcome],
) -> None:
    for o in all_outcomes:
        assert o.explanation
        assert o.decided_by == "policy"
        if o.decision != "AUTO_APPROVE":
            assert o.reasons, o.truth["invoice_id"]


def test_checks_are_persisted_for_every_invoice(all_outcomes: list[Outcome]) -> None:
    for o in all_outcomes:
        if o.truth["scenario"] == "clean":
            assert o.checks == {str(c) for c in CheckName}, o.truth["invoice_id"]
        else:
            assert o.checks, o.truth["invoice_id"]


def test_the_agent_trace_is_readable_and_within_the_step_limit(all_outcomes: list[Outcome]) -> None:
    first = all_outcomes[0]
    assert first.actions[0] == "uploaded"
    assert "extraction" in first.actions
    assert "agent_finish" in first.actions
    assert first.actions[-1] == "decision"
    assert 1 <= len(first.steps) <= get_settings().agent_max_tool_calls
    assert all("tool" in step and "tokens_in" in step for step in first.steps)
    assert all(len(o.steps) <= 8 for o in all_outcomes)


def test_the_distribution_of_outcomes(all_outcomes: list[Outcome]) -> None:
    assert Counter(o.decision for o in all_outcomes) == {
        "AUTO_APPROVE": 48,
        "NEEDS_REVIEW": 45,
        "REJECT": 7,
    }


# ------------------------------------------------------------- hostile model
def test_a_fully_manipulated_model_cannot_change_any_decision(env: Env) -> None:
    """The agent and explanation are controlled by an attacker. Decisions must not change."""
    provider = OracleProvider(env.dataset, hostile=True)
    checked = 0
    for path in sorted((env.dataset / "ground_truth").glob("*.json")):
        truth = json.loads(path.read_text("utf-8"))
        if not (truth["injection"] or truth["scenario"] in {"clean", "price_mismatch"}):
            continue
        if checked >= 14 and not truth["injection"]:
            continue
        checked += 1
        forget(env, truth["fields"]["invoice_number"])
        invoice_id = run_one(env, (env.dataset / truth["pdf"]).read_bytes(), provider)
        _, d = final(env, invoice_id)
        assert d.decision == truth["expected_decision"], truth["invoice_id"]
        if d.decision != "AUTO_APPROVE":
            assert "AUTO_APPROVE" not in d.explanation  # the model's lie was discarded
            assert "fully approved" not in d.explanation


# -------------------------------------------------------- failures route to humans
class Down:
    def generate(self, request: LLMRequest) -> LLMResponse:
        raise LLMError("connection refused")


def test_model_unavailable_sends_the_invoice_to_a_human_never_through(env: Env) -> None:
    pdf, _ = clean_pdf(env)
    inv, d = final(env, run_one(env, pdf, Down()))
    assert d.decision == "NEEDS_REVIEW"
    assert inv.status == InvoiceStatus.NEEDS_REVIEW
    assert "llm_unavailable" in {r["code"] for r in d.reasons}
    assert d.explanation  # deterministic fallback text


class Garbage:
    def generate(self, request: LLMRequest) -> LLMResponse:
        return LLMResponse(text="I'm sorry, I can't help with that.", model="m")


def test_unparseable_model_output_goes_to_review(env: Env) -> None:
    pdf, _ = clean_pdf(env)
    _, d = final(env, run_one(env, pdf, Garbage()))
    assert d.decision == "NEEDS_REVIEW"
    assert "extraction_failed" in {r["code"] for r in d.reasons}


def test_a_pdf_that_cannot_be_read_goes_to_review_without_crashing(env: Env) -> None:
    inv, d = final(env, run_one(env, b"%PDF-1.4\nthis is not really a pdf", Garbage()))
    assert d.decision == "NEEDS_REVIEW"
    assert inv.status == InvoiceStatus.NEEDS_REVIEW


def test_agent_step_limit_forces_review_even_for_a_clean_invoice(env: Env) -> None:
    pdf, _ = clean_pdf(env)
    oracle = OracleProvider(env.dataset)

    class Loops:
        """Extracts fine, then calls find_vendor forever and never finishes."""

        def generate(self, request: LLMRequest) -> LLMResponse:
            if "action" in request.schema.get("properties", {}):
                body = {"action": "call_tool", "tool": "find_vendor", "arguments": {"name": "x"}}
                return LLMResponse(text=json.dumps(body), model="m")
            return oracle.generate(request)

    invoice_id = run_one(env, pdf, Loops())
    _, d = final(env, invoice_id)
    assert d.decision == "NEEDS_REVIEW", d.reasons
    assert "agent_step_limit" in {r["code"] for r in d.reasons}
    with Session(env.engine) as s:
        calls = s.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.entity_id == invoice_id, AuditLog.action == "agent_step")
        )
    assert calls == get_settings().agent_max_tool_calls == 8


# --------------------------------------------------------------------- tools
def test_there_is_no_tool_that_can_approve_pay_or_change_anything() -> None:
    forbidden = ("approve", "reject", "pay", "update", "delete", "set_", "write", "create")
    assert not [n for n in TOOLS if any(f in n for f in forbidden)]
    assert set(TOOLS) == {
        "find_vendor", "get_purchase_order", "get_goods_receipts",
        "get_invoiced_quantities", "check_duplicates", "run_three_way_match",
    }  # fmt: skip


def test_tool_database_transactions_are_read_only(env: Env) -> None:
    with read_only_session() as s, pytest.raises(DBAPIError, match="read-only"):
        s.execute(text("UPDATE vendors SET name = 'hacked'"))
    with Session(env.engine) as s:
        assert (
            s.scalar(select(func.count()).select_from(Vendor).where(Vendor.name == "hacked")) == 0
        )


def test_malformed_tool_calls_return_structured_errors_not_crashes(env: Env) -> None:
    with read_only_session() as s:
        ctx = ToolContext(s, env.settings, invoice_id=1)
        with pytest.raises(ToolError, match="unknown tool 'approve_invoice'"):
            execute_tool(ctx, "approve_invoice", {})
        with pytest.raises(ToolError, match="provide gstin or name"):
            execute_tool(ctx, "find_vendor", {})
        with pytest.raises(ToolError, match="po_number"):
            execute_tool(ctx, "get_purchase_order", {"po_number": ""})
        with pytest.raises(ToolError, match="invalid arguments"):
            execute_tool(ctx, "check_duplicates", {"vendor_id": "abc"})
        with pytest.raises(ToolError, match="invalid arguments"):
            execute_tool(ctx, "get_purchase_order", {"po_number": "PO-1", "extra": 1})


def test_tools_can_only_match_the_invoice_being_processed(env: Env) -> None:
    with read_only_session() as s:
        ctx = ToolContext(s, env.settings, invoice_id=1)
        with pytest.raises(ToolError, match="currently being processed"):
            execute_tool(ctx, "run_three_way_match", {"invoice_id": 2})


def test_tools_return_real_data(env: Env) -> None:
    with read_only_session() as s:
        ctx = ToolContext(s, env.settings, invoice_id=1)
        vendor = s.scalars(select(Vendor).limit(1)).one()
        found = execute_tool(ctx, "find_vendor", {"gstin": vendor.gstin})
        assert found["found"]
        assert found["vendor"]["name"] == vendor.name
        assert execute_tool(ctx, "find_vendor", {"name": "No Such Vendor"}) == {"found": False}
        po = execute_tool(ctx, "get_purchase_order", {"po_number": "PO-2024-0001"})
        assert po["found"]
        assert po["lines"]
        assert execute_tool(ctx, "get_purchase_order", {"po_number": "PO-NOPE"}) == {"found": False}
        receipts = execute_tool(ctx, "get_goods_receipts", {"po_number": "PO-2024-0001"})
        assert "total_received_by_po_line" in receipts


def test_run_agent_stops_on_llm_error_without_raising(env: Env) -> None:
    with read_only_session() as s:
        trace = run_agent(Down(), "m", ToolContext(s, env.settings, 1), {}, 8)
    assert trace.outcome == "llm_error"
    assert trace.steps == []
