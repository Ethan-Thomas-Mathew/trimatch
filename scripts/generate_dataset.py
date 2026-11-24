"""Generate the synthetic invoice dataset (build plan section 8).

Writes, under ``--out`` (default ``<repo>/data/dataset``):
  invoices/TM-0001.pdf ...       100 invoice PDFs (3 layouts, 8 scanned)
  ground_truth/TM-0001.json ...  correct fields + expected decision per invoice
  manifest.json                  seed, counts and a SHA-256 of every file

The output is byte-for-byte reproducible for a given seed.

Usage:
  uv run python ../scripts/generate_dataset.py [--seed N] [--out DIR]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

from render import rasterise, render_invoice
from world import DEFAULT_SEED, World, build_world

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "data" / "dataset"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(world: World, out: Path) -> dict[str, object]:
    pdf_dir, gt_dir = out / "invoices", out / "ground_truth"
    for d in (pdf_dir, gt_dir):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)

    # A separate RNG for scan noise so rendering never shifts the world's random stream.
    scan_rng = random.Random(world.seed + 1)
    files: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        for inv in world.invoices:
            pdf_path = pdf_dir / f"{inv.invoice_id}.pdf"
            if inv.scanned:
                digital = Path(tmp) / f"{inv.invoice_id}.pdf"
                render_invoice(inv, world.buyer, digital)
                rasterise(digital, pdf_path, scan_rng)
            else:
                render_invoice(inv, world.buyer, pdf_path)

            gt = inv.ground_truth()
            gt["pdf"] = f"invoices/{pdf_path.name}"
            gt_path = gt_dir / f"{inv.invoice_id}.json"
            gt_path.write_text(json.dumps(gt, indent=2, ensure_ascii=False) + "\n", "utf-8")
            files[gt["pdf"]] = _sha256(pdf_path)
            files[f"ground_truth/{gt_path.name}"] = _sha256(gt_path)

    manifest = {
        "seed": world.seed,
        "invoice_count": len(world.invoices),
        "scenarios": dict(sorted(Counter(i.scenario for i in world.invoices).items())),
        "expected_decisions": dict(
            sorted(Counter(str(i.expected_decision) for i in world.invoices).items())
        ),
        "layouts": dict(sorted(Counter(i.layout for i in world.invoices).items())),
        "scanned": sum(i.scanned for i in world.invoices),
        "files": dict(sorted(files.items())),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", "utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    manifest = generate(build_world(args.seed), args.out)
    print(f"Wrote {manifest['invoice_count']} invoices to {args.out}")
    print(f"  scenarios: {manifest['scenarios']}")
    print(f"  expected decisions: {manifest['expected_decisions']}")
    print(f"  layouts: {manifest['layouts']}, scanned: {manifest['scanned']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
