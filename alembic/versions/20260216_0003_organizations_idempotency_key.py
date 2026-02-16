"""Expand-only: organizations.idempotency_key UNIQUE."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260216_0003"
down_revision: str | None = "20260216_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
    )
    op.create_unique_constraint(
        "uq_organizations_idempotency_key",
        "organizations",
        ["idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_organizations_idempotency_key", "organizations", type_="unique")
    op.drop_column("organizations", "idempotency_key")
