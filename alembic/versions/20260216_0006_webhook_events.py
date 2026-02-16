"""Expand-only: webhook_events table (persist-first ingestion)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260216_0006"
down_revision: str | None = "20260216_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

webhook_event_status = postgresql.ENUM(
    "received",
    "processing",
    "processed",
    "failed",
    "skipped",
    name="webhook_event_status",
    create_type=False,
)


def upgrade() -> None:
    op.execute(
        "CREATE TYPE webhook_event_status AS ENUM "
        "('received', 'processing', 'processed', 'failed', 'skipped')"
    )
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("provider_event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            webhook_event_status,
            server_default=sa.text("'received'"),
            nullable=False,
        ),
        sa.Column(
            "processing_attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_event_id"),
    )


def downgrade() -> None:
    op.drop_table("webhook_events")
    op.execute("DROP TYPE webhook_event_status")
