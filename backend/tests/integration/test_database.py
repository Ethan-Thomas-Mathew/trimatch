"""Schema, migration and seed integration tests (need Postgres)."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
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

from .conftest import alembic_config

pytestmark = pytest.mark.integration

EXPECTED_TABLES = {
    "vendors", "purchase_orders", "po_lines", "goods_receipts", "grn_lines", "invoices",
    "invoice_lines", "extraction_runs", "match_results", "decisions", "review_actions",
    "audit_log",
}  # fmt: skip


def test_models_cover_every_table_in_the_spec() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_migration_creates_exactly_the_model_tables(migrated_engine: Engine) -> None:
    tables = set(inspect(migrated_engine).get_table_names()) - {"alembic_version"}
    assert tables == EXPECTED_TABLES


def test_models_and_migration_are_in_sync(migrated_engine: Engine) -> None:
    """Autogenerate against the migrated DB must find nothing left to add."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    with migrated_engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(ctx, Base.metadata) == []


def test_money_columns_are_exact_decimals(migrated_engine: Engine) -> None:
    cols = {c["name"]: c for c in inspect(migrated_engine).get_columns("invoices")}
    for name in ("subtotal", "tax_total", "grand_total"):
        assert str(cols[name]["type"]) == "NUMERIC(14, 2)"


def test_migration_is_reversible(database_url: str) -> None:
    cfg = alembic_config(database_url)
    command.downgrade(cfg, "base")
    engine = create_engine(database_url)
    try:
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    finally:
        engine.dispose()
    command.upgrade(cfg, "head")


def test_decision_check_constraint_rejects_unknown_values(migrated_engine: Engine) -> None:
    with Session(migrated_engine) as s, pytest.raises(IntegrityError):  # noqa: PT012
        inv = Invoice(status="PROCESSING")
        s.add(inv)
        s.flush()
        s.add(DecisionRecord(invoice_id=inv.id, decision="MAYBE", decided_by="policy", reasons=[]))
        s.flush()


def test_duplicate_invoice_numbers_can_be_stored(migrated_engine: Engine) -> None:
    """Exact duplicates must be persisted so they can be rejected and audited."""
    with Session(migrated_engine) as s:
        v = Vendor(name="Dup Test Vendor", gstin="29ZZZZZ9999Z1ZZ", state_code="29")
        s.add(v)
        s.flush()
        s.add_all(
            Invoice(vendor_id=v.id, invoice_number="X/1", status="PROCESSING") for _ in range(2)
        )
        s.flush()  # no unique violation
        s.rollback()


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def seeded_engine(migrated_engine: Engine) -> Engine:
    from seed import run

    counts = run(migrated_engine)
    assert counts == {
        "vendors": 20,
        "purchase_orders": 150,
        "goods_receipts": counts["goods_receipts"],
        "historical_invoices": 8,
    }
    return migrated_engine


def test_seed_counts(seeded_engine: Engine) -> None:
    with Session(seeded_engine) as s:
        assert s.scalar(select(func.count()).select_from(Vendor)) == 20
        assert s.scalar(select(func.count()).select_from(PurchaseOrder)) == 150
        assert (s.scalar(select(func.count()).select_from(GoodsReceipt)) or 0) >= 150 * 0.9
        assert s.scalar(select(func.count()).select_from(Invoice)) == 8


def test_seed_is_idempotent(seeded_engine: Engine) -> None:
    from seed import run

    run(seeded_engine)
    run(seeded_engine)
    with Session(seeded_engine) as s:
        assert s.scalar(select(func.count()).select_from(Vendor)) == 20
        assert s.scalar(select(func.count()).select_from(Invoice)) == 8


def test_received_quantities_never_exceed_ordered(seeded_engine: Engine) -> None:
    with Session(seeded_engine) as s:
        received: dict[int, Decimal] = defaultdict(lambda: Decimal(0))
        for line_id, qty in s.execute(select(GRNLine.po_line_id, GRNLine.qty_received)):
            received[line_id] += qty
        ordered = {r.id: r.qty for r in s.scalars(select(POLine))}
        assert received
        assert all(q <= ordered[i] for i, q in received.items())


def test_grn_lines_belong_to_their_receipts_po(seeded_engine: Engine) -> None:
    with seeded_engine.connect() as conn:
        mismatched = conn.execute(
            text(
                "SELECT count(*) FROM grn_lines gl "
                "JOIN goods_receipts g ON g.id = gl.grn_id "
                "JOIN po_lines pl ON pl.id = gl.po_line_id "
                "WHERE pl.po_id <> g.po_id"
            )
        ).scalar_one()
    assert mismatched == 0


def test_historical_invoices_are_consistent(seeded_engine: Engine) -> None:
    with Session(seeded_engine) as s:
        for inv in s.scalars(select(Invoice)):
            assert inv.status == "APPROVED"
            lines = s.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)).all()
            assert lines
            assert inv.subtotal == sum((ln.line_total for ln in lines), start=Decimal(0))
            assert inv.grand_total == inv.subtotal + inv.tax_total  # type: ignore[operator]


def test_seed_refuses_a_non_local_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings
    from seed import main

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@prod.example.com:5432/x")
    get_settings.cache_clear()
    try:
        assert main([]) == 2
    finally:
        get_settings.cache_clear()
