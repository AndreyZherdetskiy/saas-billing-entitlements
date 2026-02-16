"""Expand-only: outbox_dead_letters for poison messages (ADR-001)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260216_0007"
down_revision: str | None = "20260216_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_dead_letters",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("outbox_message_id", sa.BigInteger(), nullable=False),
        sa.Column("aggregate_type", sa.String(length=128), nullable=False),
        sa.Column("aggregate_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("partition_key", sa.String(length=128), nullable=False),
        sa.Column("publish_attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "moved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_dead_letters_outbox_message_id",
        "outbox_dead_letters",
        ["outbox_message_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outbox_dead_letters_outbox_message_id",
        table_name="outbox_dead_letters",
    )
    op.drop_table("outbox_dead_letters")
