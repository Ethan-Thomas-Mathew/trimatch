"""Seed the database with vendors, purchase orders, goods receipts and historical invoices.

The data comes from ``world.build_world(seed)``: the same seed that generates the invoice
PDFs, so the database and the dataset always agree.

Seeding replaces all existing data (TRUNCATE ... RESTART IDENTITY). As a guard against
wiping a real database, it refuses to run against a non-local host unless ``--force``.

Usage:
  uv run python ../scripts/seed.py [--seed N] [--force]
"""

from __future__ import annotations

import argparse
import sys
from decimal import Decimal

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    AuditLog,
    Base,
    DecisionRecord,
    GoodsReceipt,
    GRNLine,
    Invoice,
    InvoiceLine,
    POLine,
    PurchaseOrder,
    Vendor,
)
from app.schemas.enums import DecidedBy, Decision, InvoiceStatus
from world import DEFAULT_SEED, World, build_world

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres", None}


def reset(session: Session) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def seed(session: Session, world: World) -> dict[str, int]:
    """Insert the world's master data and history. Caller owns the transaction."""
    vendor_rows: dict[int, Vendor] = {}
    for v in world.vendors:
        row = Vendor(name=v.party.name, gstin=v.party.gstin, state_code=v.party.state_code)
        session.add(row)
        vendor_rows[v.index] = row
    session.flush()

    po_line_rows: dict[tuple[str, int], POLine] = {}
    n_grn = 0
    for po in sorted(world.pos, key=lambda p: p.po_number):
        po_row = PurchaseOrder(
            po_number=po.po_number,
            vendor_id=vendor_rows[po.vendor.index].id,
            created_at=po.created_at,
            status=str(po.status),
        )
        session.add(po_row)
        session.flush()
        for i, ln in enumerate(po.lines):
            line_row = POLine(
                po_id=po_row.id,
                sku=ln.sku,
                description=ln.description,
                qty=ln.qty,
                unit_price=ln.unit_price,
                tax_rate=ln.tax_rate,
            )
            session.add(line_row)
            po_line_rows[(po.po_number, i)] = line_row
        session.flush()
        for grn in po.grns:
            grn_row = GoodsReceipt(
                grn_number=grn.grn_number, po_id=po_row.id, received_at=grn.received_at
            )
            session.add(grn_row)
            session.flush()
            n_grn += 1
            for i, qty in grn.lines:
                session.add(
                    GRNLine(
                        grn_id=grn_row.id,
                        po_line_id=po_line_rows[(po.po_number, i)].id,
                        qty_received=qty,
                    )
                )

    # Historical invoices: already processed and approved before the dataset period.
    # They are what the duplicate checks and the "already invoiced" quantity check find.
    for inv in world.historical_invoices:
        assert inv.po is not None
        inv_row = Invoice(
            vendor_id=vendor_rows[inv.vendor.index].id,
            invoice_number=inv.invoice_number,
            invoice_date=inv.invoice_date,
            po_number=inv.po.po_number,
            subtotal=inv.subtotal,
            tax_total=inv.tax_total,
            grand_total=inv.grand_total,
            file_path=None,
            status=str(InvoiceStatus.APPROVED),
        )
        session.add(inv_row)
        session.flush()
        for ln in inv.lines:
            assert ln.po_line_index is not None
            session.add(
                InvoiceLine(
                    invoice_id=inv_row.id,
                    po_line_id=po_line_rows[(inv.po.po_number, ln.po_line_index)].id,
                    sku=ln.sku,
                    description=ln.description,
                    qty=ln.qty,
                    unit_price=ln.unit_price,
                    line_total=ln.line_total,
                )
            )
        session.add(
            DecisionRecord(
                invoice_id=inv_row.id,
                decision=str(Decision.AUTO_APPROVE),
                decided_by=str(DecidedBy.POLICY),
                reasons=[],
                explanation="Historical invoice loaded by the seed script.",
            )
        )
        session.add(
            AuditLog(
                entity="invoice",
                entity_id=inv_row.id,
                action="seeded",
                actor="seed_script",
                payload={"invoice_number": inv.invoice_number, "seed": world.seed},
            )
        )

    session.add(
        AuditLog(
            entity="database",
            entity_id=None,
            action="seeded",
            actor="seed_script",
            payload={
                "seed": world.seed,
                "grand_total_sum_historical": str(
                    sum((i.grand_total for i in world.historical_invoices), start=Decimal("0"))
                ),
            },
        )
    )
    session.flush()
    return {
        "vendors": len(world.vendors),
        "purchase_orders": len(world.pos),
        "goods_receipts": n_grn,
        "historical_invoices": len(world.historical_invoices),
    }


def run(engine: Engine, seed_value: int = DEFAULT_SEED) -> dict[str, int]:
    world = build_world(seed_value)
    with Session(engine) as session, session.begin():
        reset(session)
        return seed(session, world)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the TriMatch database.")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow seeding a non-local database (destroys its data)",
    )
    args = parser.parse_args(argv)

    url = get_settings().database_url
    host = make_url(url).host
    if host not in LOCAL_HOSTS and not args.force:
        print(
            f"Refusing to wipe and seed non-local database host {host!r}; "
            "pass --force if you mean it.",
            file=sys.stderr,
        )
        return 2

    counts = run(create_engine(url), args.seed)
    print("Seeded: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
