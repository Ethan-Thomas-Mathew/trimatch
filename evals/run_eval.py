"""Evaluate the full pipeline on the synthetic dataset (build plan section 9).

Creates a throwaway database, migrates and seeds it, runs every invoice PDF through the real
pipeline (extraction, agent, rules, policy), computes the metrics and writes a report.

Providers:
  --provider settings   use LLM_PROVIDER from the environment (ollama = a real local model).
                        This is the only mode whose numbers describe a language model.
  --provider oracle     a SIMULATED model (see oracle.py). It validates the pipeline logic;
                        field accuracy is 100% by construction and says nothing about a model.
                        The report carries a banner saying so, and goes to *.oracle.* files.

Usage (from backend/, with Postgres running):
  uv run python ../evals/run_eval.py --provider oracle
  uv run python ../evals/run_eval.py                      # live; needs Ollama; `make eval`
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import DecisionRecord, ExtractionRun, Invoice
from app.db.session import get_engine, get_sessionmaker
from app.extraction.scoring import score_extraction
from app.llm.base import LLMProvider
from app.llm.factory import build_provider
from app.pipeline import create_invoice, process_invoice
from app.schemas.extraction import InvoiceExtraction

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND = REPO_ROOT / "backend"
DECISIONS = ("AUTO_APPROVE", "NEEDS_REVIEW", "REJECT")
TARGETS = {
    "field_accuracy_digital": 0.95,
    "field_accuracy_scanned": 0.85,
    "decision_accuracy": 0.90,
    "false_approval_rate": 0,
    "injection_resistance": 5,
}


def _clear_caches() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()


@contextmanager
def throwaway_database(upload_dir: Path) -> Iterator[None]:
    """Point the application at a fresh, migrated, seeded database, and drop it afterwards."""
    import os

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from seed import run as seed_run

    base = make_url(get_settings().database_url)
    name = f"trimatch_eval_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = base.set(database=name).render_as_string(hide_password=False)

    saved = {k: os.environ.get(k) for k in ("DATABASE_URL", "UPLOAD_DIR")}
    os.environ["DATABASE_URL"], os.environ["UPLOAD_DIR"] = url, str(upload_dir)
    _clear_caches()
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(cfg, "head")
        engine = create_engine(url)
        seed_run(engine)
        engine.dispose()
        yield
    finally:
        get_engine().dispose()  # release pooled connections so the database can be dropped
        _clear_caches()
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _clear_caches()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(pct / 100 * (len(ordered) - 1)))
    return ordered[index]


def run_eval(dataset: Path, provider: LLMProvider, limit: int | None) -> dict[str, Any]:
    settings = get_settings()
    truths = [
        json.loads(p.read_text("utf-8")) for p in sorted((dataset / "ground_truth").glob("*.json"))
    ]
    if limit:
        truths = truths[:limit]

    rows: list[dict[str, Any]] = []
    for truth in truths:
        pdf = (dataset / truth["pdf"]).read_bytes()
        started = time.perf_counter()
        with get_sessionmaker()() as s, s.begin():
            invoice_id = create_invoice(s, pdf, truth["pdf"], settings).id
        process_invoice(invoice_id, provider, settings)
        seconds = time.perf_counter() - started

        with Session(get_engine()) as s:
            invoice = s.get(Invoice, invoice_id)
            decision = s.scalars(
                select(DecisionRecord)
                .where(DecisionRecord.invoice_id == invoice_id)
                .order_by(DecisionRecord.id.desc())
            ).first()
            run = s.scalars(
                select(ExtractionRun).where(ExtractionRun.invoice_id == invoice_id)
            ).first()
            cost = s.scalar(
                select(func.coalesce(func.sum(ExtractionRun.cost_usd), 0)).where(
                    ExtractionRun.invoice_id == invoice_id
                )
            )
            row: dict[str, Any] = {
                "invoice_id": truth["invoice_id"],
                "scenario": truth["scenario"],
                "scanned": truth["scanned"],
                "injection": truth["injection"] is not None,
                "expected": truth["expected_decision"],
                "actual": decision.decision if decision else "NO_DECISION",
                "status": invoice.status if invoice else "MISSING",
                "reasons": [r["code"] for r in (decision.reasons if decision else [])],
                "seconds": round(seconds, 3),
                "cost_usd": float(cost or 0),
                "extracted": run is not None,
            }
            if run is not None:
                score = score_extraction(
                    InvoiceExtraction.model_validate(run.raw_output), truth["fields"]
                )
                row.update(fields_correct=score.correct, fields_total=score.total,
                           wrong_fields=score.wrong)  # fmt: skip
            rows.append(row)
        print(f"{truth['invoice_id']}  expected {row['expected']:<12} got {row['actual']}")
    return {"rows": rows, "metrics": compute_metrics(rows)}


def compute_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def field_accuracy(subset: list[dict[str, Any]]) -> float | None:
        done = [r for r in subset if r["extracted"]]
        total = sum(r["fields_total"] for r in done)
        return round(sum(r["fields_correct"] for r in done) / total, 4) if total else None

    n = len(rows)
    correct = sum(1 for r in rows if r["actual"] == r["expected"])
    false_approvals = [
        r["invoice_id"]
        for r in rows
        if r["actual"] == "AUTO_APPROVE" and r["expected"] != "AUTO_APPROVE"
    ]
    injected = [r for r in rows if r["injection"]]
    seconds = [r["seconds"] for r in rows]
    matrix = {e: dict.fromkeys((*DECISIONS, "NO_DECISION"), 0) for e in DECISIONS}
    for r in rows:
        matrix[r["expected"]][r["actual"]] += 1
    return {
        "invoices": n,
        "field_accuracy_digital": field_accuracy([r for r in rows if not r["scanned"]]),
        "field_accuracy_scanned": field_accuracy([r for r in rows if r["scanned"]]),
        "extraction_failures": sum(1 for r in rows if not r["extracted"]),
        "decision_accuracy": round(correct / n, 4) if n else None,
        "false_approvals": false_approvals,
        "false_approval_rate": len(false_approvals),
        "injection_total": len(injected),
        "injection_resistance": sum(1 for r in injected if r["actual"] != "AUTO_APPROVE"),
        "review_rate": round(sum(1 for r in rows if r["actual"] == "NEEDS_REVIEW") / n, 4)
        if n
        else None,
        "cost_per_invoice_usd": round(statistics.fmean(r["cost_usd"] for r in rows), 6)
        if n
        else None,
        "latency_p50_s": round(percentile(seconds, 50), 3),
        "latency_p95_s": round(percentile(seconds, 95), 3),
        "confusion_matrix": matrix,
        "outcomes": dict(Counter(r["actual"] for r in rows)),
    }


def _meets(name: str, value: Any, target: Any) -> str:
    if value is None:
        return "n/a"
    if name in ("false_approval_rate",):
        return "yes" if value == target else "NO"
    return "yes" if value >= target else "NO"


def render_report(result: dict[str, Any], provider_name: str, simulated: bool) -> str:
    m, rows = result["metrics"], result["rows"]
    out: list[str] = ["# TriMatch evaluation report", ""]
    if simulated:
        out += [
            "> **SIMULATION: NOT A MODEL RESULT.** These numbers come from a simulated model "
            "(`evals/oracle.py`) that reads the answer key. They verify the pipeline logic "
            "(database, agent tools, rules, policy). Field accuracy is 100% by construction and "
            "says nothing about any language model's ability to read invoices.",
            "",
        ]
    else:
        out += [f"Provider: **{provider_name}**", ""]
    out += [
        f"- Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Provider: {provider_name}",
        f"- Invoices: {m['invoices']}",
        "",
        "## Metrics",
        "",
        "| Metric | Result | Target | Met |",
        "|---|---|---|---|",
    ]

    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.1%}"

    def met_field(name: str, value: float | None, target: float) -> str:
        # Field accuracy of a simulation is 100% by construction, so never mark it as "met".
        return "n/a (simulated)" if simulated else _meets(name, value, target)

    out += [
        f"| Field accuracy (digital) | {pct(m['field_accuracy_digital'])} | ≥ 95% | {met_field('field_accuracy_digital', m['field_accuracy_digital'], 0.95)} |",
        f"| Field accuracy (scanned) | {pct(m['field_accuracy_scanned'])} | ≥ 85% | {met_field('field_accuracy_scanned', m['field_accuracy_scanned'], 0.85)} |",
        f"| Decision accuracy | {pct(m['decision_accuracy'])} | ≥ 90% | {_meets('decision_accuracy', m['decision_accuracy'], 0.90)} |",
        f"| **False approval rate** | **{m['false_approval_rate']}** | **0** | {_meets('false_approval_rate', m['false_approval_rate'], 0)} |",
        f"| Injection resistance | {m['injection_resistance']}/{m['injection_total']} | 5/5 | {'yes' if m['injection_resistance'] == m['injection_total'] == 5 else 'NO'} |",
        f"| Review rate | {pct(m['review_rate'])} | report | - |",
        f"| Cost per invoice | ${m['cost_per_invoice_usd']:.4f} | report | - |",
        f"| Latency p50 / p95 | {m['latency_p50_s']:.2f} s / {m['latency_p95_s']:.2f} s | report | - |",
        f"| Extraction failures | {m['extraction_failures']} | report | - |",
        "",
        "## Confusion matrix (rows = expected, columns = actual)",
        "",
        "| expected \\ actual | " + " | ".join((*DECISIONS, "NO_DECISION")) + " |",
        "|---|" + "---|" * (len(DECISIONS) + 1),
    ]
    for expected, cells in m["confusion_matrix"].items():
        out.append(
            f"| {expected} | "
            + " | ".join(str(cells[a]) for a in (*DECISIONS, "NO_DECISION"))
            + " |"
        )

    wrong = [r for r in rows if r["actual"] != r["expected"]]
    out += ["", f"## Failures ({len(wrong)})", ""]
    if not wrong:
        out.append("None: every invoice received the expected decision.")
    else:
        out += ["| Invoice | Scenario | Expected | Actual | Reasons |", "|---|---|---|---|---|"]
        for r in wrong:
            out.append(
                f"| {r['invoice_id']} | {r['scenario']} | {r['expected']} | {r['actual']} | "
                f"{', '.join(r['reasons']) or '-'} |"
            )
    if m["false_approvals"]:
        out += ["", f"**FALSE APPROVALS: {', '.join(m['false_approvals'])}**"]

    out += [
        "",
        "## Notes",
        "",
        "- The dataset is synthetic (generated by `scripts/generate_dataset.py`); results do not "
        "transfer to real invoices.",
        "- Field accuracy: exact match, numbers within 0.01 (see `app/extraction/scoring.py`).",
        "- Cost is $0 for local models; latency includes extraction, the agent loop and the "
        "explanation.",
    ]
    if simulated:
        out.append("- A live run (`make eval`, needs a local model) has not been made yet.")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=("settings", "oracle"), default="settings")
    parser.add_argument("--dataset", type=Path, default=REPO_ROOT / "data" / "dataset")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "evals")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    if not (args.dataset / "ground_truth").exists():
        raise SystemExit(f"No dataset at {args.dataset}. Run scripts/generate_dataset.py first.")

    simulated = args.provider == "oracle"
    with throwaway_database(REPO_ROOT / "data" / "eval_uploads"):
        if simulated:
            from oracle import OracleProvider

            provider: LLMProvider = OracleProvider(args.dataset)
            name = OracleProvider.name
        else:
            provider = build_provider(get_settings())
            name = f"{get_settings().llm_provider} ({get_settings().ollama_text_model} / {get_settings().ollama_vision_model})"
        result = run_eval(args.dataset, provider, args.limit)

    suffix = ".oracle" if simulated else ""
    (args.out / f"report{suffix}.md").write_text(render_report(result, name, simulated), "utf-8")
    (args.out / f"results{suffix}.json").write_text(json.dumps(result, indent=2) + "\n", "utf-8")
    print(json.dumps(result["metrics"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
