"""allow a human APPROVE decision

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-24
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "'AUTO_APPROVE', 'NEEDS_REVIEW', 'REJECT'"
_NEW = "'AUTO_APPROVE', 'NEEDS_REVIEW', 'REJECT', 'APPROVE'"


def upgrade() -> None:
    # op.f() marks the name as final so the naming convention is not applied a second time.
    op.drop_constraint(op.f("ck_decisions_decision"), "decisions", type_="check")
    op.create_check_constraint("decision", "decisions", f"decision IN ({_NEW})")


def downgrade() -> None:
    op.execute("UPDATE decisions SET decision = 'AUTO_APPROVE' WHERE decision = 'APPROVE'")
    op.drop_constraint(op.f("ck_decisions_decision"), "decisions", type_="check")
    op.create_check_constraint("decision", "decisions", f"decision IN ({_OLD})")
