"""Expand-only: dunning_campaigns (ADR-008)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260216_0015"
down_revision: str | None = "20260216_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dunning_campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("grace_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "policy_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_dunning_campaigns_subscription_id",
        "dunning_campaigns",
        ["subscription_id"],
        unique=False,
    )
    op.create_index(
        "ix_dunning_campaigns_organization_id",
        "dunning_campaigns",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "uq_dunning_campaigns_active_subscription",
        "dunning_campaigns",
        ["subscription_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_dunning_campaigns_active_subscription",
        table_name="dunning_campaigns",
    )
    op.drop_index("ix_dunning_campaigns_organization_id", table_name="dunning_campaigns")
    op.drop_index("ix_dunning_campaigns_subscription_id", table_name="dunning_campaigns")
    op.drop_table("dunning_campaigns")
