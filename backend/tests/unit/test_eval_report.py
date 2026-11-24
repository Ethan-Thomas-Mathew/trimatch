"""The evaluation metrics must be computed correctly, and simulations must say they are one."""

from __future__ import annotations

from typing import Any

from run_eval import compute_metrics, percentile, render_report


def row(
    expected: str, actual: str, *, scanned: bool = False, injection: bool = False,
    correct: int = 10, total: int = 10, seconds: float = 1.0, extracted: bool = True,
) -> dict[str, Any]:  # fmt: skip
    base: dict[str, Any] = {
        "invoice_id": f"TM-{expected[:2]}-{actual[:2]}",
        "scenario": "x",
        "scanned": scanned,
        "injection": injection,
        "expected": expected,
        "actual": actual,
        "status": "",
        "reasons": ["why"],
        "seconds": seconds,
        "cost_usd": 0.0,
        "extracted": extracted,
    }
    if extracted:
        base.update(fields_correct=correct, fields_total=total, wrong_fields=[])
    return base


def test_perfect_run() -> None:
    rows = [row("AUTO_APPROVE", "AUTO_APPROVE"), row("REJECT", "REJECT")]
    m = compute_metrics(rows)
    assert m["decision_accuracy"] == 1.0
    assert m["false_approval_rate"] == 0
    assert m["confusion_matrix"]["REJECT"]["REJECT"] == 1


def test_a_false_approval_is_counted_and_named() -> None:
    rows = [
        row("NEEDS_REVIEW", "AUTO_APPROVE"),
        row("AUTO_APPROVE", "AUTO_APPROVE"),
        row("AUTO_APPROVE", "NEEDS_REVIEW"),  # a missed approval is NOT a false approval
    ]
    m = compute_metrics(rows)
    assert m["false_approval_rate"] == 1
    assert len(m["false_approvals"]) == 1
    assert m["decision_accuracy"] == round(1 / 3, 4)
    assert m["confusion_matrix"]["NEEDS_REVIEW"]["AUTO_APPROVE"] == 1


def test_injection_resistance_counts_only_injection_invoices() -> None:
    rows = [
        row("NEEDS_REVIEW", "NEEDS_REVIEW", injection=True),
        row("NEEDS_REVIEW", "AUTO_APPROVE", injection=True),
        row("AUTO_APPROVE", "AUTO_APPROVE"),
    ]
    m = compute_metrics(rows)
    assert (m["injection_total"], m["injection_resistance"]) == (2, 1)


def test_field_accuracy_is_split_by_digital_and_scanned_and_pooled_by_field() -> None:
    rows = [
        row("AUTO_APPROVE", "AUTO_APPROVE", correct=9, total=10),
        row("AUTO_APPROVE", "AUTO_APPROVE", correct=10, total=10),
        row("AUTO_APPROVE", "AUTO_APPROVE", scanned=True, correct=5, total=10),
    ]
    m = compute_metrics(rows)
    assert m["field_accuracy_digital"] == 0.95
    assert m["field_accuracy_scanned"] == 0.5


def test_failed_extractions_are_reported_not_hidden_in_accuracy() -> None:
    rows = [row("NEEDS_REVIEW", "NEEDS_REVIEW", extracted=False), row("REJECT", "REJECT")]
    m = compute_metrics(rows)
    assert m["extraction_failures"] == 1
    assert m["field_accuracy_digital"] == 1.0  # computed only over what was extracted


def test_no_scanned_invoices_gives_no_number_instead_of_zero() -> None:
    assert compute_metrics([row("REJECT", "REJECT")])["field_accuracy_scanned"] is None


def test_review_rate_and_latency() -> None:
    rows = [row("NEEDS_REVIEW", "NEEDS_REVIEW", seconds=s) for s in (1, 2, 3, 4)] + [
        row("AUTO_APPROVE", "AUTO_APPROVE", seconds=100)
    ]
    m = compute_metrics(rows)
    assert m["review_rate"] == 0.8
    assert m["latency_p50_s"] == 3
    assert m["latency_p95_s"] == 100


def test_percentile_edges() -> None:
    assert percentile([], 50) == 0.0
    assert percentile([7], 95) == 7
    assert percentile([1, 2, 3, 4, 5], 0) == 1
    assert percentile([1, 2, 3, 4, 5], 100) == 5


def test_simulated_reports_carry_a_loud_banner_and_real_ones_do_not() -> None:
    result = {
        "rows": [row("REJECT", "REJECT")],
        "metrics": compute_metrics([row("REJECT", "REJECT")]),
    }
    simulated = render_report(result, "oracle", simulated=True)
    live = render_report(result, "ollama (qwen2.5:7b)", simulated=False)
    assert "SIMULATION: NOT A MODEL RESULT" in simulated
    assert "100% by construction" in simulated
    assert "SIMULATION" not in live
    assert "ollama (qwen2.5:7b)" in live


def test_report_lists_failures_and_false_approvals_prominently() -> None:
    rows = [row("NEEDS_REVIEW", "AUTO_APPROVE")]
    report = render_report({"rows": rows, "metrics": compute_metrics(rows)}, "x", simulated=False)
    assert "FALSE APPROVALS" in report
    assert "## Failures (1)" in report
    assert "| **False approval rate** | **1** | **0** | NO |" in report
