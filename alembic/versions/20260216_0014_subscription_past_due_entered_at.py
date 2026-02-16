"""Expand-only: subscriptions.past_due_entered_at for grace clock."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260216_0014"
down_revision: str | None = "20260216_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("past_due_entered_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "past_due_entered_at")
