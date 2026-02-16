"""Expand-only ZDT drill: invoices.zdt_drill_marker (ADR-009); contract DROP deferred."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260216_0017"
down_revision: str | None = "20260216_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "invoices",
        sa.Column("zdt_drill_marker", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("invoices", "zdt_drill_marker")
