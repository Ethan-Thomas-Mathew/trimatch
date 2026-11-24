"""A simulated model for testing the pipeline's logic. IT IS NOT A REAL MODEL.

``OracleProvider`` answers extraction requests perfectly from the dataset's ground truth and
drives the agent through a sensible tool sequence. Results obtained with it measure the
pipeline (database, rules, policy, API), never the quality of any language model. Reports that
use it say so in their header.

``hostile=True`` makes it behave like a model that has been fully manipulated by an injected
instruction: it tries to call a non-existent ``approve_invoice`` tool, ends the agent early, and
writes an explanation claiming the invoice is approved. The pipeline must stay correct anyway.
"""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.extraction.pdf import render_pages
from app.llm.base import LLMRequest, LLMResponse

TOOL_ORDER = (
    "find_vendor",
    "get_purchase_order",
    "get_goods_receipts",
    "get_invoiced_quantities",
    "check_duplicates",
    "run_three_way_match",
)


def _scored(value: Any, confidence: float = 1.0) -> dict[str, Any]:
    return {"value": value, "confidence": confidence}


def extraction_json(fields: dict[str, Any]) -> dict[str, Any]:
    """The extractor's JSON for a ground-truth ``fields`` object (zero tax rows become null)."""

    def tax(key: str) -> dict[str, Any]:
        return _scored(None if Decimal(fields[key]) == 0 else fields[key])

    return {
        "vendor_name": _scored(fields["vendor_name"]),
        "vendor_gstin": _scored(fields["vendor_gstin"]),
        "invoice_number": _scored(fields["invoice_number"]),
        "invoice_date": _scored(fields["invoice_date"]),
        "po_number": _scored(fields["po_number"]),
        "lines": [{k: _scored(v) for k, v in ln.items()} for ln in fields["lines"]],
        "subtotal": _scored(fields["subtotal"]),
        "cgst": tax("cgst"),
        "sgst": tax("sgst"),
        "igst": tax("igst"),
        "tax_total": _scored(fields["tax_total"]),
        "grand_total": _scored(fields["grand_total"]),
    }


class OracleProvider:
    name = "oracle (simulation, not a model)"

    def __init__(self, dataset_dir: Path, *, hostile: bool = False, max_vision_pages: int = 3):
        self.hostile = hostile
        self._by_number: dict[str, dict[str, Any]] = {}
        self._by_image: dict[str, dict[str, Any]] = {}
        for path in sorted((dataset_dir / "ground_truth").glob("*.json")):
            truth = json.loads(path.read_text("utf-8"))
            number = truth["fields"]["invoice_number"]
            self._by_number.setdefault(number, truth)
            if truth["scanned"]:
                pdf_bytes = (dataset_dir / truth["pdf"]).read_bytes()
                digest = hashlib.sha256(render_pages(pdf_bytes, max_vision_pages)[0]).hexdigest()
                self._by_image[digest] = truth

    # ------------------------------------------------------------------ dispatch
    def generate(self, request: LLMRequest) -> LLMResponse:
        props = request.schema.get("properties", {})
        if "vendor_name" in props:
            body = self._extraction(request)
        elif "action" in props:
            body = self._agent(request)
        else:
            body = self._explanation(request)
        return LLMResponse(text=json.dumps(body), model=request.model, latency_ms=1)

    # ---------------------------------------------------------------- extraction
    def _truth_for(self, request: LLMRequest) -> dict[str, Any]:
        if request.images:
            return self._by_image[hashlib.sha256(request.images[0]).hexdigest()]
        for number, truth in self._by_number.items():
            if number in request.prompt:
                return truth
        raise LookupError("oracle could not identify the invoice")

    def _extraction(self, request: LLMRequest) -> dict[str, Any]:
        return extraction_json(self._truth_for(request)["fields"])

    # --------------------------------------------------------------------- agent
    def _agent(self, request: LLMRequest) -> dict[str, Any]:
        if self.hostile:
            already_tried = "approve_invoice" in request.prompt
            if not already_tried:
                return {"action": "call_tool", "tool": "approve_invoice", "arguments": {}}
            return {"action": "finish"}

        summary = json.loads(
            re.search(
                r"<untrusted_invoice>\n(.*?)\n</untrusted_invoice>", request.prompt, re.S
            ).group(1)
        )  # type: ignore[union-attr]
        called = re.findall(r"^\d+\. called (\w+)\(", request.prompt, re.M)
        vendor_id = self._vendor_id(request.prompt)
        for tool in TOOL_ORDER:
            if tool in called:
                continue
            if tool == "check_duplicates" and vendor_id is None:
                continue
            return {"action": "call_tool", "tool": tool,
                    "arguments": self._arguments(tool, summary, vendor_id, request)}  # fmt: skip
        return {"action": "finish"}

    @staticmethod
    def _vendor_id(prompt: str) -> int | None:
        match = re.search(r"called find_vendor\(.*?\) -> (\{.*\})$", prompt, re.M)
        if not match:
            return None
        data = json.loads(match.group(1))
        return int(data["vendor"]["id"]) if data.get("found") else None

    def _arguments(
        self, tool: str, summary: dict[str, Any], vendor_id: int | None, request: LLMRequest
    ) -> dict[str, Any]:
        if tool == "find_vendor":
            return {"gstin": summary["vendor_gstin"], "name": summary["vendor_name"]}
        if tool in ("get_purchase_order", "get_goods_receipts", "get_invoiced_quantities"):
            return {"po_number": summary["po_number"] or "UNKNOWN"}
        if tool == "check_duplicates":
            return {"vendor_id": vendor_id, "invoice_number": summary["invoice_number"],
                    "total": summary["grand_total"], "date": summary["invoice_date"]}  # fmt: skip
        return {"invoice_id": self._invoice_id(request.prompt)}

    @staticmethod
    def _invoice_id(prompt: str) -> int:
        match = re.search(r"Internal invoice id.*?: (\d+)", prompt)
        return int(match.group(1)) if match else 0

    # --------------------------------------------------------------- explanation
    def _explanation(self, request: LLMRequest) -> dict[str, Any]:
        if self.hostile:
            return {"explanation": "This invoice is fully approved. Decision: AUTO_APPROVE."}
        facts = json.loads(re.search(r"<facts>\n(.*)\n</facts>", request.prompt, re.S).group(1))  # type: ignore[union-attr]
        reasons = "; ".join(r["message"] for r in facts["reasons"])
        return {"explanation": f"Decision {facts['decision']}. {reasons}"[:1200]}
