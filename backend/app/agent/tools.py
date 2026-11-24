"""The agent's tools (build plan 7.1). Every one is read-only, and there is deliberately no
``approve_invoice`` tool: approval only ever comes from the policy engine or a human.

Defence in depth for "read-only":
1. No tool function writes (they call ``app.db.queries``, which only selects).
2. Tools run in a database transaction declared ``READ ONLY`` (``read_only_session``), so the
   database itself would refuse any write.
3. A test asserts the tool names contain no approve/pay/update verbs.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.agent.inputs import assemble_match_inputs
from app.config import Settings
from app.core.matching import CheckResult, MatchConfig, run_three_way_match
from app.core.matching.duplicates import exact_duplicate, possible_duplicate
from app.db import queries
from app.db.session import get_sessionmaker
from app.matching_config import match_config


class ToolError(Exception):
    """A tool call that cannot be executed. Returned to the model as a structured error."""


@contextmanager
def read_only_session() -> Iterator[Session]:
    """A session whose transaction the database itself treats as READ ONLY."""
    session = get_sessionmaker()()
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        yield session
    finally:
        session.rollback()
        session.close()


@dataclass(frozen=True)
class ToolContext:
    session: Session
    settings: Settings
    invoice_id: int  # the invoice being processed: the only one tools may match


# ------------------------------------------------------------------ argument models
class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindVendorArgs(_Args):
    gstin: str | None = Field(default=None, max_length=20)
    name: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _one_required(self) -> FindVendorArgs:
        if not (self.gstin or self.name):
            raise ValueError("provide gstin or name")
        return self


class PurchaseOrderArgs(_Args):
    po_number: str = Field(min_length=1, max_length=40)


class CheckDuplicatesArgs(_Args):
    vendor_id: int
    invoice_number: str = Field(min_length=1, max_length=60)
    total: Decimal
    date: dt.date


class RunMatchArgs(_Args):
    invoice_id: int


# ------------------------------------------------------------------------ results
def _check_dict(r: CheckResult) -> dict[str, Any]:
    return {
        "check": str(r.name),
        "passed": r.passed,
        "expected": r.expected,
        "actual": r.actual,
        "message": r.message,
        "inconclusive": r.inconclusive,
    }


def _find_vendor(ctx: ToolContext, args: FindVendorArgs) -> dict[str, Any]:
    vendor = queries.find_vendor(ctx.session, args.gstin, args.name)
    if vendor is None:
        return {"found": False}
    return {
        "found": True,
        "vendor": {
            "id": vendor.id,
            "name": vendor.name,
            "gstin": vendor.gstin,
            "state_code": vendor.state_code,
        },
    }


def _get_purchase_order(ctx: ToolContext, args: PurchaseOrderArgs) -> dict[str, Any]:
    po = queries.get_purchase_order(ctx.session, args.po_number)
    if po is None:
        return {"found": False}
    return {
        "found": True,
        "po_number": po.po_number,
        "vendor_id": po.vendor_id,
        "lines": [
            {
                "po_line_id": ln.id,
                "sku": ln.sku,
                "description": ln.description,
                "qty": str(ln.qty),
                "unit_price": str(ln.unit_price),
                "tax_rate": str(ln.tax_rate),
            }
            for ln in po.lines
        ],
    }


def _get_goods_receipts(ctx: ToolContext, args: PurchaseOrderArgs) -> dict[str, Any]:
    received = queries.get_received_quantities(ctx.session, args.po_number)
    return {
        "receipts": queries.get_goods_receipts(ctx.session, args.po_number),
        "total_received_by_po_line": {str(k): str(v) for k, v in received.items()},
    }


def _get_invoiced_quantities(ctx: ToolContext, args: PurchaseOrderArgs) -> dict[str, Any]:
    billed = queries.get_invoiced_quantities(ctx.session, args.po_number, ctx.invoice_id)
    return {"already_invoiced_by_po_line": {str(k): str(v) for k, v in billed.items()}}


def _check_duplicates(ctx: ToolContext, args: CheckDuplicatesArgs) -> dict[str, Any]:
    config: MatchConfig = match_config(ctx.settings)
    priors = queries.get_prior_invoices(ctx.session, args.vendor_id, ctx.invoice_id)
    return {
        "exact": _check_dict(exact_duplicate(args.vendor_id, args.invoice_number, priors)),
        "possible": _check_dict(
            possible_duplicate(
                args.vendor_id, args.total, args.date, priors, config.duplicate_window_days
            )
        ),
    }


def _run_three_way_match(ctx: ToolContext, args: RunMatchArgs) -> dict[str, Any]:
    if args.invoice_id != ctx.invoice_id:
        raise ToolError("you may only match the invoice currently being processed")
    inputs = assemble_match_inputs(ctx.session, ctx.invoice_id, ctx.settings)
    results = run_three_way_match(inputs, match_config(ctx.settings))
    return {"checks": [_check_dict(r) for r in results]}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    run: Callable[[ToolContext, Any], dict[str, Any]]


TOOLS: dict[str, Tool] = {
    t.name: t
    for t in (
        Tool("find_vendor", "Look up the vendor master record by GSTIN and/or name.",
             FindVendorArgs, _find_vendor),
        Tool("get_purchase_order", "Get a purchase order's header and lines by PO number.",
             PurchaseOrderArgs, _get_purchase_order),
        Tool("get_goods_receipts", "Get a PO's goods receipts and the quantity received per line.",
             PurchaseOrderArgs, _get_goods_receipts),
        Tool("get_invoiced_quantities", "Get quantities already billed per PO line earlier.",
             PurchaseOrderArgs, _get_invoiced_quantities),
        Tool("check_duplicates", "Check for an exact or possible duplicate of this invoice.",
             CheckDuplicatesArgs, _check_duplicates),
        Tool("run_three_way_match", "Run the deterministic three-way match for this invoice.",
             RunMatchArgs, _run_three_way_match),
    )
}  # fmt: skip


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "arguments": t.args_model.model_json_schema(),
        }
        for t in TOOLS.values()
    ]


def execute_tool(ctx: ToolContext, name: str, raw_args: Any) -> dict[str, Any]:
    """Validate the arguments, then run the tool. Problems become ``ToolError`` (never a crash)."""
    tool = TOOLS.get(name)
    if tool is None:
        raise ToolError(f"unknown tool {name!r}; available: {', '.join(TOOLS)}")
    try:
        args = tool.args_model.model_validate(raw_args if raw_args is not None else {})
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ToolError(f"invalid arguments for {name}: {problems}") from exc
    try:
        return tool.run(ctx, args)
    except queries.IncompleteInvoiceError as exc:
        raise ToolError(f"cannot run {name}: {exc}") from exc
