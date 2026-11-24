"""Compare an extraction with ground truth (used for the Phase 2 accuracy number and Phase 5).

Rules, stated so the metric is honest and reproducible:
- Text fields match after case-folding and collapsing whitespace.
- Numbers match within 0.01 (build plan 9).
- Dates must be equal.
- A tax row (cgst/sgst/igst) that is absent from the invoice counts as 0.00, so ``null`` and
  ``0.00`` are the same answer.
- Lines are compared by position. A missing or extra line makes every field of it wrong.
- The score is exact matches / total fields, where the total is the 11 scalar fields plus
  5 fields for each ground-truth line (and for each extra line the model invented).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.schemas.extraction import LINE_FIELDS, SCALAR_FIELDS, InvoiceExtraction

TOLERANCE = Decimal("0.01")
TAX_FIELDS = {"cgst", "sgst", "igst"}
NUMERIC = {"subtotal", "cgst", "sgst", "igst", "tax_total", "grand_total", "qty", "unit_price",
           "line_total"}  # fmt: skip


@dataclass
class FieldScore:
    correct: int = 0
    total: int = 0
    wrong: list[str] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


def _norm_text(v: Any) -> str:
    return " ".join(str(v).split()).casefold() if v is not None else ""


def _matches(name: str, extracted: Any, truth: Any) -> bool:
    if name in NUMERIC:
        if extracted is None and truth is None:
            return True
        if name in TAX_FIELDS:
            extracted = Decimal(0) if extracted is None else extracted
            truth = Decimal(0) if truth is None else truth
        if extracted is None or truth is None:
            return False
        return abs(Decimal(str(extracted)) - Decimal(str(truth))) <= TOLERANCE
    if name == "invoice_date":
        parsed = dt.date.fromisoformat(truth) if isinstance(truth, str) else truth
        return bool(extracted == parsed)
    return _norm_text(extracted) == _norm_text(truth)


def score_extraction(extraction: InvoiceExtraction, truth_fields: dict[str, Any]) -> FieldScore:
    """``truth_fields`` is the ``fields`` object of a ground-truth JSON file."""
    result = FieldScore()

    def record(label: str, ok: bool) -> None:
        result.total += 1
        if ok:
            result.correct += 1
        else:
            result.wrong.append(label)

    for name in SCALAR_FIELDS:
        record(name, _matches(name, getattr(extraction, name).value, truth_fields.get(name)))

    truth_lines = truth_fields.get("lines", [])
    for i in range(max(len(truth_lines), len(extraction.lines))):
        for name in LINE_FIELDS:
            if i >= len(truth_lines) or i >= len(extraction.lines):
                record(f"lines.{i}.{name}", False)
            else:
                record(
                    f"lines.{i}.{name}",
                    _matches(name, getattr(extraction.lines[i], name).value, truth_lines[i][name]),
                )
    return result
