"""The HTTP API end to end: auth, upload, list, detail, file, review, metrics, safety."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_provider
from app.db.models import AuditLog, DecisionRecord, Invoice, ReviewAction
from app.llm.base import LLMRequest, LLMResponse
from app.main import create_app
from oracle import OracleProvider
from tests.integration.conftest import Env, dataset_invoice

pytestmark = pytest.mark.integration

AUTH = ("reviewer", "test-password")


@pytest.fixture(scope="module")
def client(env: Env) -> Iterator[TestClient]:
    app = create_app()
    oracle = OracleProvider(env.dataset)
    app.dependency_overrides[get_provider] = lambda: oracle
    with TestClient(app) as c:
        yield c


def upload(client: TestClient, pdf: bytes, name: str = "invoice.pdf") -> dict[str, Any]:
    response = client.post("/invoices", files={"file": (name, pdf, "application/pdf")}, auth=AUTH)
    assert response.status_code == 202, response.text
    return response.json()  # type: ignore[no-any-return]


def detail(client: TestClient, invoice_id: int) -> dict[str, Any]:
    response = client.get(f"/invoices/{invoice_id}", auth=AUTH)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


# ------------------------------------------------------------------- auth
def test_health_needs_no_login_but_everything_else_does(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json() == {"status": "ready"}
    for path in ("/invoices", "/invoices/1", "/metrics", "/invoices/1/file"):
        assert client.get(path).status_code == 401, path
    assert client.post("/invoices/1/review", json={}).status_code == 401
    assert client.post("/invoices", files={"file": ("a.pdf", b"%PDF-")}).status_code == 401


def test_wrong_credentials_are_refused_without_saying_which_part_was_wrong(
    client: TestClient,
) -> None:
    for creds in (("reviewer", "nope"), ("someone", "test-password"), ("", "")):
        r = client.get("/invoices", auth=creds)
        assert r.status_code == 401
        assert r.json() == {"detail": "Invalid credentials."}
        assert "Basic" in r.headers["www-authenticate"]


def test_the_web_ui_never_triggers_the_browsers_native_login_box(client: TestClient) -> None:
    """Regression: a wrong password made the browser pop up its own 'Sign in' dialog."""
    ui = {"X-Requested-With": "TriMatch-UI"}
    for creds in (None, ("reviewer", "wrong")):
        r = client.get("/invoices", auth=creds, headers=ui)
        assert r.status_code == 401
        assert "www-authenticate" not in r.headers
        assert r.json() == {"detail": "Invalid credentials."}
    # A plain API client still receives the standard challenge.
    plain = client.get("/invoices")
    assert plain.status_code == 401
    assert plain.headers["www-authenticate"].startswith("Basic")
    # And the UI marker does not bypass authentication.
    assert client.get("/invoices", headers=ui, auth=AUTH).status_code == 200


def test_security_headers_and_request_id(client: TestClient) -> None:
    r = client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-request-id"]


def test_cors_only_allows_the_configured_origin(client: TestClient) -> None:
    ok = client.options(
        "/invoices",
        headers={"Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "GET"},
    )
    bad = client.options(
        "/invoices",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert ok.headers.get("access-control-allow-origin") == "http://127.0.0.1:5173"
    assert "access-control-allow-origin" not in bad.headers


# ----------------------------------------------------------------- upload
def test_upload_a_clean_invoice_is_auto_approved_in_the_background(
    client: TestClient, env: Env
) -> None:
    pdf, truth = dataset_invoice(
        env, scenario="clean", scanned=False, over_auto_approve_limit=False
    )
    created = upload(client, pdf)
    assert created["status"] == "PROCESSING"  # the response does not wait for processing
    d = detail(client, created["id"])
    assert d["status"] == "AUTO_APPROVED"
    assert d["decision"]["decided_by"] == "policy"
    assert d["invoice_number"] == truth["fields"]["invoice_number"]
    assert len(d["checks"]) == 11
    assert all(c["passed"] for c in d["checks"])
    assert d["extraction"]["fields"]["grand_total"]["confidence"] == 1.0
    assert next(t["action"] for t in d["trace"]) == "uploaded"


def test_upload_of_a_problem_invoice_needs_review_with_an_explanation(
    client: TestClient, env: Env
) -> None:
    pdf, _ = dataset_invoice(env, scenario="price_mismatch", scanned=False)
    d = detail(client, upload(client, pdf)["id"])
    assert d["status"] == "NEEDS_REVIEW"
    failed = [c for c in d["checks"] if not c["passed"]]
    assert [c["check"] for c in failed] == ["unit_price"]
    assert d["decision"]["explanation"]
    assert d["decision"]["reasons"][0]["code"] == "unit_price"


def test_only_pdfs_are_accepted(client: TestClient) -> None:
    for content, name, expected in (
        (b"", "empty.pdf", 400),
        (b"hello, I am not a pdf", "notes.pdf", 415),
        (b"<html><script>alert(1)</script></html>", "evil.pdf", 415),
        (b"\x89PNG\r\n\x1a\n", "image.pdf", 415),
    ):
        r = client.post("/invoices", files={"file": (name, content, "application/pdf")}, auth=AUTH)
        assert r.status_code == expected, (name, r.text)


def test_oversized_upload_is_rejected(client: TestClient) -> None:
    big = b"%PDF-1.4\n" + b"0" * (11 * 1024 * 1024)
    r = client.post("/invoices", files={"file": ("big.pdf", big, "application/pdf")}, auth=AUTH)
    assert r.status_code == 413


def test_missing_file_field_is_a_validation_error(client: TestClient) -> None:
    assert client.post("/invoices", auth=AUTH).status_code == 422


def test_client_filename_never_touches_the_disk_and_is_sanitised_in_the_audit_log(
    client: TestClient, env: Env
) -> None:
    pdf, _ = dataset_invoice(env, scenario="qty_over_billed", scanned=False)
    created = upload(client, pdf, name="../../../etc/passwd;rm -rf *.pdf")
    with Session(env.engine) as s:
        invoice = s.get(Invoice, created["id"])
        assert invoice is not None
        assert invoice.file_path is not None
        assert "/" not in invoice.file_path
        assert len(invoice.file_path) == 36  # 32 hex characters + ".pdf"
        event = s.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == created["id"], AuditLog.action == "uploaded"
            )
        ).one()
        assert "/" not in event.payload["original_name"]
        assert ";" not in event.payload["original_name"]


def test_a_pipeline_crash_leaves_a_reviewable_failed_invoice(client: TestClient, env: Env) -> None:
    class Explodes:
        def generate(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError("boom")  # not an LLMError: an unexpected bug

    client.app.dependency_overrides[get_provider] = lambda: Explodes()  # type: ignore[attr-defined]
    try:
        pdf, _ = dataset_invoice(env, scenario="clean", scanned=False, over_auto_approve_limit=True)
        created = upload(client, pdf)
    finally:
        client.app.dependency_overrides[get_provider] = lambda: OracleProvider(env.dataset)  # type: ignore[attr-defined]
    assert detail(client, created["id"])["status"] == "FAILED"


# ------------------------------------------------------------ list / detail
def test_list_filters_by_status_and_paginates(client: TestClient, env: Env) -> None:
    for scenario in ("price_mismatch", "qty_over_billed", "gst_arithmetic"):
        _needs_review(client, env, scenario)
    everything = client.get("/invoices?limit=200", auth=AUTH).json()
    assert everything["total"] == len(everything["items"]) >= 4
    review = client.get("/invoices?status=NEEDS_REVIEW", auth=AUTH).json()
    assert review["items"]
    assert all(i["status"] == "NEEDS_REVIEW" for i in review["items"])
    page = client.get("/invoices?limit=2&offset=1", auth=AUTH).json()
    assert len(page["items"]) == 2
    assert page["total"] == everything["total"]
    ids = [i["id"] for i in everything["items"]]
    assert ids == sorted(ids, reverse=True)  # newest first


def test_list_rejects_unknown_status_and_bad_paging(client: TestClient) -> None:
    assert client.get("/invoices?status=BOGUS", auth=AUTH).status_code == 422
    assert client.get("/invoices?limit=0", auth=AUTH).status_code == 422
    assert client.get("/invoices?limit=500", auth=AUTH).status_code == 422
    assert client.get("/invoices?offset=-1", auth=AUTH).status_code == 422


def test_detail_of_a_missing_invoice_is_404(client: TestClient) -> None:
    assert client.get("/invoices/999999", auth=AUTH).status_code == 404
    assert client.get("/invoices/abc", auth=AUTH).status_code == 422


def test_pdf_is_served_inline_with_safe_headers(client: TestClient, env: Env) -> None:
    pdf, _ = dataset_invoice(env, scenario="gstin_mismatch")
    created = upload(client, pdf)
    r = client.get(f"/invoices/{created['id']}/file", auth=AUTH)
    assert r.status_code == 200
    assert r.content == pdf
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == "inline"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_seeded_history_has_no_file(client: TestClient, env: Env) -> None:
    with Session(env.engine) as s:
        historical = s.scalars(select(Invoice).where(Invoice.file_path.is_(None))).first()
        assert historical is not None
        hid = historical.id
    assert client.get(f"/invoices/{hid}/file", auth=AUTH).status_code == 404
    assert client.get("/invoices/999999/file", auth=AUTH).status_code == 404


# ----------------------------------------------------------------- review
def _needs_review(client: TestClient, env: Env, scenario: str) -> int:
    pdf, _ = dataset_invoice(env, scenario=scenario, scanned=False)
    created = upload(client, pdf)
    assert detail(client, created["id"])["status"] == "NEEDS_REVIEW"
    return int(created["id"])


def test_reviewer_can_approve_with_a_comment_and_it_is_audited(
    client: TestClient, env: Env
) -> None:
    invoice_id = _needs_review(client, env, "gst_wrong_tax_type")
    r = client.post(
        f"/invoices/{invoice_id}/review",
        json={"action": "approve", "comment": "Supplier confirmed the tax split by phone."},
        auth=AUTH,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "APPROVED"
    assert body["decision"]["decided_by"] == "human"
    assert body["decision"]["decision"] == "APPROVE"
    assert body["reviews"][0]["comment"].startswith("Supplier confirmed")
    with Session(env.engine) as s:
        assert s.scalars(select(ReviewAction).where(ReviewAction.invoice_id == invoice_id)).one()
        decisions = s.scalars(
            select(DecisionRecord).where(DecisionRecord.invoice_id == invoice_id)
        ).all()
        assert [d.decided_by for d in decisions] == ["policy", "human"]  # history is kept
        audit_event = s.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == invoice_id, AuditLog.action == "review_approve"
            )
        ).one()
        assert audit_event.actor == "reviewer"
        assert audit_event.payload["comment"].startswith("Supplier confirmed")


def test_reviewer_can_reject(client: TestClient, env: Env) -> None:
    invoice_id = _needs_review(client, env, "gst_arithmetic")
    r = client.post(
        f"/invoices/{invoice_id}/review",
        json={"action": "reject", "comment": "Totals are wrong, ask for a corrected invoice."},
        auth=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["status"] == "REJECTED"


def test_a_comment_is_required(client: TestClient, env: Env) -> None:
    invoice_id = _needs_review(client, env, "missing_po")
    for body in (
        {"action": "approve", "comment": ""},
        {"action": "approve", "comment": "    \n "},
        {"action": "approve"},
        {"action": "reject", "comment": "x" * 2001},
        {"action": "maybe", "comment": "hmm"},
        {"action": "approve", "comment": "ok", "status": "APPROVED"},
    ):
        r = client.post(f"/invoices/{invoice_id}/review", json=body, auth=AUTH)
        assert r.status_code == 422, body
    assert detail(client, invoice_id)["status"] == "NEEDS_REVIEW"  # nothing changed


def _review(client: TestClient, invoice_id: int, action: str, comment: str, **extra: Any) -> Any:
    return client.post(
        f"/invoices/{invoice_id}/review",
        json={"action": action, "comment": comment, **extra},
        auth=AUTH,
    )


def test_a_decided_invoice_cannot_be_decided_the_same_way_twice(
    client: TestClient, env: Env
) -> None:
    invoice_id = _needs_review(client, env, "possible_duplicate")
    assert _review(client, invoice_id, "reject", "Looks like a duplicate.").status_code == 200
    again = _review(client, invoice_id, "reject", "still a duplicate")
    assert again.status_code == 409
    assert "can only be approved" in again.json()["detail"]
    assert detail(client, invoice_id)["status"] == "REJECTED"


def test_a_human_rejected_invoice_can_be_approved_later(client: TestClient, env: Env) -> None:
    invoice_id = _needs_review(client, env, "price_mismatch")
    assert _review(client, invoice_id, "reject", "Price looks wrong.").status_code == 200

    later = _review(client, invoice_id, "approve", "Supplier sent a credit note; price is fine.")
    assert later.status_code == 200, later.text  # no acknowledgement needed: a person rejected it
    body = later.json()
    assert body["status"] == "APPROVED"
    assert body["decision"]["decision"] == "APPROVE"
    assert body["decision"]["decided_by"] == "human"
    assert [r["action"] for r in body["reviews"]] == ["reject", "approve_override"]

    with Session(env.engine) as s:
        event = s.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == invoice_id, AuditLog.action == "review_approve_override"
            )
        ).one()
        assert event.payload["reversal"] is True
        assert event.payload["previous_status"] == "REJECTED"
        assert event.payload["overrode_policy_reasons"] == []
        decisions = s.scalars(
            select(DecisionRecord).where(DecisionRecord.invoice_id == invoice_id)
        ).all()
        assert [d.decided_by for d in decisions] == ["policy", "human", "human"]  # full history

    # Once approved it is done: it cannot be approved or rejected again.
    assert _review(client, invoice_id, "approve", "again").status_code == 409
    assert _review(client, invoice_id, "reject", "again").status_code == 409


def test_overriding_a_rules_engine_rejection_needs_an_explicit_acknowledgement(
    client: TestClient, env: Env
) -> None:
    pdf, _ = dataset_invoice(env, scenario="gstin_invalid_format")
    invoice_id = upload(client, pdf)["id"]
    assert detail(client, invoice_id)["status"] == "REJECTED"

    for extra in ({}, {"acknowledge_override": False}):
        refused = _review(client, invoice_id, "approve", "please", **extra)
        assert refused.status_code == 409
        assert "rules engine rejected" in refused.json()["detail"]
    assert detail(client, invoice_id)["status"] == "REJECTED"  # nothing changed

    ok = _review(
        client, invoice_id, "approve", "Supplier confirmed their GSTIN by phone.",
        acknowledge_override=True,
    )  # fmt: skip
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "APPROVED"
    with Session(env.engine) as s:
        event = s.scalars(
            select(AuditLog).where(
                AuditLog.entity_id == invoice_id, AuditLog.action == "review_approve_override"
            )
        ).one()
        assert "gstin_format" in event.payload["overrode_policy_reasons"]


def test_the_acknowledgement_does_not_let_you_reject_a_rejected_invoice(
    client: TestClient, env: Env
) -> None:
    pdf, _ = dataset_invoice(env, scenario="exact_duplicate")
    invoice_id = upload(client, pdf)["id"]
    r = _review(client, invoice_id, "reject", "x", acknowledge_override=True)
    assert r.status_code == 409


def test_a_duplicate_can_be_overridden_only_with_acknowledgement(
    client: TestClient, env: Env
) -> None:
    pdf, _ = dataset_invoice(env, scenario="exact_duplicate")
    invoice_id = upload(client, pdf)["id"]
    assert detail(client, invoice_id)["status"] == "REJECTED"
    assert _review(client, invoice_id, "approve", "ok").status_code == 409
    assert (
        _review(client, invoice_id, "approve", "Original was voided.", acknowledge_override=True)
        .status_code == 200
    )  # fmt: skip


def test_an_auto_approved_invoice_cannot_be_reviewed(client: TestClient, env: Env) -> None:
    pdf, _ = dataset_invoice(env, scenario="clean", scanned=True)
    created = upload(client, pdf)
    assert detail(client, created["id"])["status"] == "AUTO_APPROVED"
    r = client.post(
        f"/invoices/{created['id']}/review",
        json={"action": "reject", "comment": "no"},
        auth=AUTH,
    )
    assert r.status_code == 409


def test_review_of_an_unknown_invoice_is_404(client: TestClient) -> None:
    r = client.post(
        "/invoices/999999/review", json={"action": "approve", "comment": "x"}, auth=AUTH
    )
    assert r.status_code == 404


# ----------------------------------------------------------------- metrics
def test_metrics_add_up(client: TestClient, env: Env) -> None:
    invoice_id = _needs_review(client, env, "gst_wrong_tax_type")
    approved = client.post(
        f"/invoices/{invoice_id}/review",
        json={"action": "approve", "comment": "Checked with the supplier."},
        auth=AUTH,
    )
    assert approved.status_code == 200
    m = client.get("/metrics", auth=AUTH).json()
    listing = client.get("/invoices?limit=200", auth=AUTH).json()
    assert m["invoices"] == listing["total"]
    assert sum(m["by_status"].values()) == m["invoices"]
    assert m["awaiting_review"] == m["by_status"].get("NEEDS_REVIEW", 0) + m["by_status"].get(
        "FAILED", 0
    )
    assert m["by_decision"].get("APPROVE", 0) >= 1  # a human approval from the tests above
    assert m["avg_cost_usd"] == 0
    assert m["avg_cost_inr"] == 0  # zero needs no exchange rate
    assert m["avg_pipeline_seconds"] > 0
