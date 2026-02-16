"""Expand-only: outbox_dead_letters.replayed_at for idempotent DLQ replay (ADR-009)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260216_0018"
down_revision: str | None = "20260216_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "outbox_dead_letters",
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("outbox_dead_letters", "replayed_at")
