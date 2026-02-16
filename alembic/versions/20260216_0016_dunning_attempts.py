"""Expand-only: dunning_attempts (ADR-008 amendment)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260216_0016"
down_revision: str | None = "20260216_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dunning_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.String(length=32), nullable=True),
        sa.Column("external_charge_id", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["campaign_id"], ["dunning_campaigns.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("campaign_id", "attempt_no", name="uq_dunning_attempts_campaign_no"),
    )
    op.create_index(
        "ix_dunning_attempts_campaign_id",
        "dunning_attempts",
        ["campaign_id"],
        unique=False,
    )
    op.create_index(
        "ix_dunning_attempts_due",
        "dunning_attempts",
        ["scheduled_at"],
        unique=False,
        postgresql_where=sa.text("executed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_dunning_attempts_due", table_name="dunning_attempts")
    op.drop_index("ix_dunning_attempts_campaign_id", table_name="dunning_attempts")
    op.drop_table("dunning_attempts")
