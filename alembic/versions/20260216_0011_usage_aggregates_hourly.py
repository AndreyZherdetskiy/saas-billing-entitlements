"""Expand-only: usage_aggregates_hourly table."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260216_0011"
down_revision: str | None = "20260216_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_aggregates_hourly",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("public_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("feature_key", sa.String(length=255), nullable=False),
        sa.Column("hour_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "feature_key",
            "hour_start",
            name="uq_usage_aggregates_hourly_org_feature_hour",
        ),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_usage_aggregates_hourly_org_feature_hour",
        "usage_aggregates_hourly",
        ["organization_id", "feature_key", "hour_start"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_usage_aggregates_hourly_org_feature_hour",
        table_name="usage_aggregates_hourly",
    )
    op.drop_table("usage_aggregates_hourly")
