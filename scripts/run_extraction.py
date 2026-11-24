"""Run extraction over the synthetic dataset and report field accuracy (build plan Phase 2).

Needs a local LLM: install Ollama and pull the models in OLLAMA_TEXT_MODEL / OLLAMA_VISION_MODEL
(see README). No API key is involved, and the host must be this machine.

Usage (from backend/):
  uv run python ../scripts/run_extraction.py [--limit N] [--ids TM-0001 TM-0002] [--record DIR]

  --record DIR  also save every model answer as a replay fixture in DIR (used by CI)
Writes per-invoice results and summary.json under data/extraction_results/.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.extraction.scoring import score_extraction
from app.extraction.service import ExtractionFailedError, extract_invoice
from app.llm.base import LLMError, LLMProvider
from app.llm.factory import build_provider
from app.llm.replay import RecordingProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATASET = REPO_ROOT / "data" / "dataset"
DEFAULT_OUT = REPO_ROOT / "data" / "extraction_results"


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def accuracy(subset: list[dict[str, Any]]) -> float | None:
        correct = sum(r["correct"] for r in subset)
        total = sum(r["total"] for r in subset)
        return round(correct / total, 4) if total else None

    done = [r for r in rows if r["status"] == "ok"]
    digital = [r for r in done if not r["scanned"]]
    scanned = [r for r in done if r["scanned"]]
    latencies = sorted(r["latency_ms"] for r in done)
    return {
        "invoices": len(rows),
        "extracted": len(done),
        "failed": len(rows) - len(done),
        "field_accuracy_digital": accuracy(digital),
        "field_accuracy_scanned": accuracy(scanned),
        "retried_after_invalid_output": sum(1 for r in done if r["attempts"] > 1),
        "latency_ms_p50": int(statistics.median(latencies)) if latencies else None,
        "latency_ms_max": latencies[-1] if latencies else None,
        "targets": {"digital": 0.95, "scanned": 0.85},
    }


def run(
    dataset: Path, out: Path, provider: LLMProvider, ids: list[str] | None, limit: int | None
) -> dict[str, Any]:
    settings = get_settings()
    truths = sorted((dataset / "ground_truth").glob("*.json"))
    if ids:
        truths = [p for p in truths if p.stem in set(ids)]
    if limit:
        truths = truths[:limit]
    if not truths:
        raise SystemExit(f"no ground truth found under {dataset}; run generate_dataset.py first")

    out.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for path in truths:
        truth = json.loads(path.read_text("utf-8"))
        row: dict[str, Any] = {
            "invoice_id": truth["invoice_id"],
            "scanned": truth["scanned"],
            "scenario": truth["scenario"],
        }
        try:
            result = extract_invoice((dataset / truth["pdf"]).read_bytes(), provider, settings)
        except (ExtractionFailedError, LLMError) as exc:
            row.update(status="failed", error=str(exc)[:300])
        else:
            score = score_extraction(result.extraction, truth["fields"])
            row.update(
                status="ok",
                mode=result.mode,
                model=result.model,
                attempts=result.attempts,
                correct=score.correct,
                total=score.total,
                accuracy=round(score.accuracy, 4),
                wrong_fields=score.wrong,
                min_confidence=result.extraction.min_confidence(),
                latency_ms=result.latency_ms,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
            )
        (out / f"{truth['invoice_id']}.json").write_text(json.dumps(row, indent=2) + "\n", "utf-8")
        rows.append(row)
        label = f"{row['accuracy']:.0%}" if row["status"] == "ok" else f"FAILED: {row['error']}"
        print(f"{row['invoice_id']}  {'scan   ' if row['scanned'] else 'digital'}  {label}")
        if (
            row["status"] == "failed"
            and "after retries" in row["error"]
            and not any(r["status"] == "ok" for r in rows)
        ):
            print(
                "  -> Could not reach the model. Is Ollama running (`ollama serve`) and are the "
                f"models pulled ({settings.ollama_text_model}, {settings.ollama_vision_model})?"
            )

    summary = summarise(rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", "utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--record", type=Path, help="directory to save replay fixtures into")
    args = parser.parse_args(argv)

    provider = build_provider(get_settings())
    if args.record:
        provider = RecordingProvider(provider, args.record)
    summary = run(args.dataset, args.out, provider, args.ids, args.limit)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
