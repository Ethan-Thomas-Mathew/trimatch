"""The structured shape of an extracted invoice (build plan section 5 and Phase 2).

Every scalar field is a ``Scored`` value: the extracted value (or ``None`` if the invoice
does not show it) plus the model's self-reported confidence between 0 and 1.

Two safety properties are deliberate:
- There is no "decision", "approve" or free-text instruction field. Whatever an injected
  instruction inside an invoice says, the schema gives it nowhere to land.
- Money is ``Decimal``. Messy numbers a model may emit ("Rs. 1,23,456.78") are cleaned
  before validation, and anything that is not a number fails validation instead of
  silently becoming 0.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, WithJsonSchema

_NUMBER_NOISE = re.compile(r"(?i)(rs\.?|inr|₹|\s|,)")
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%d-%b-%Y")


def _clean_number(v: Any) -> Any:
    if v is None or isinstance(v, Decimal):
        return v
    if isinstance(v, bool):
        raise ValueError("a boolean is not a number")
    if isinstance(v, int | float):
        return Decimal(str(v))
    if isinstance(v, str):
        cleaned = _NUMBER_NOISE.sub("", v)
        if cleaned in {"", "-"}:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"not a number: {v!r}") from exc
    raise ValueError(f"not a number: {v!r}")


def _parse_date(v: Any) -> Any:
    if v is None or isinstance(v, dt.date):
        return v
    if isinstance(v, str):
        text = v.strip()
        for fmt in _DATE_FORMATS:
            try:
                return dt.datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    raise ValueError(f"unrecognised date: {v!r} (expected YYYY-MM-DD)")


def _clean_text(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        return " ".join(v.split()) or None
    if isinstance(v, int | float) and not isinstance(v, bool):
        return str(v)
    raise ValueError(f"expected text, got {v!r}")


Money = Annotated[Decimal, BeforeValidator(_clean_number), WithJsonSchema({"type": "number"})]
Text = Annotated[str, BeforeValidator(_clean_text), WithJsonSchema({"type": "string"})]
Date = Annotated[
    dt.date,
    BeforeValidator(_parse_date),
    WithJsonSchema({"type": "string", "format": "date"}),
]


class Scored[T](BaseModel):
    """A value and how sure the extractor says it is (0 = guess, 1 = certain)."""

    model_config = ConfigDict(extra="forbid")

    value: T | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExtractedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: Scored[Text]
    description: Scored[Text]
    qty: Scored[Money]
    unit_price: Scored[Money]
    line_total: Scored[Money]


SCALAR_FIELDS = (
    "vendor_name",
    "vendor_gstin",
    "invoice_number",
    "invoice_date",
    "po_number",
    "subtotal",
    "cgst",
    "sgst",
    "igst",
    "tax_total",
    "grand_total",
)
LINE_FIELDS = ("sku", "description", "qty", "unit_price", "line_total")


class InvoiceExtraction(BaseModel):
    """What the extractor returns. Unknown keys are rejected, not ignored."""

    model_config = ConfigDict(extra="forbid")

    vendor_name: Scored[Text]
    vendor_gstin: Scored[Text]
    invoice_number: Scored[Text]
    invoice_date: Scored[Date]
    po_number: Scored[Text]
    lines: list[ExtractedLine] = Field(default_factory=list)
    subtotal: Scored[Money]
    cgst: Scored[Money]
    sgst: Scored[Money]
    igst: Scored[Money]
    tax_total: Scored[Money]
    grand_total: Scored[Money]

    def confidences(self) -> dict[str, float]:
        """Flat map like ``{"invoice_number": 0.98, "lines.0.qty": 0.9}``."""
        out = {name: getattr(self, name).confidence for name in SCALAR_FIELDS}
        for i, line in enumerate(self.lines):
            for name in LINE_FIELDS:
                out[f"lines.{i}.{name}"] = getattr(line, name).confidence
        return out

    def min_confidence(self) -> float:
        """The lowest confidence of any field (the policy engine compares this to the minimum).

        A field the invoice legitimately does not show (for example IGST on an intra-state
        invoice) is reported as ``null`` and is not counted against the invoice.
        """
        values = [conf for name, conf in self.confidences().items() if not self._is_absent(name)]
        return min(values, default=0.0)

    def _is_absent(self, flat_name: str) -> bool:
        if flat_name in {"cgst", "sgst", "igst"}:
            return getattr(self, flat_name).value is None
        return False


def llm_json_schema() -> dict[str, Any]:
    """The JSON Schema handed to the model, with ``$ref`` indirection inlined.

    Small local models and constrained-decoding backends handle a flat, self-contained
    schema far more reliably than one full of ``$defs``.
    """
    schema = InvoiceExtraction.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(defs[node["$ref"].split("/")[-1]])
            return {k: inline(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [inline(x) for x in node]
        return node

    result: dict[str, Any] = inline(schema)
    return result
