"""Expand-only: monthly RANGE-partitioned usage_events (ADR-011)."""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260216_0010"
down_revision: str | None = "20260216_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        return start, datetime(year + 1, 1, 1, tzinfo=UTC)
    return start, datetime(year, month + 1, 1, tzinfo=UTC)


def _create_partition(year: int, month: int) -> None:
    start, end = _month_bounds(year, month)
    partition_name = f"usage_events_{year:04d}_{month:02d}"
    op.execute(
        sa.text(
            f"""
            CREATE TABLE IF NOT EXISTS {partition_name}
            PARTITION OF usage_events
            FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_{partition_name}_org_idempotency
            ON {partition_name} (organization_id, idempotency_key)
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("public_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=True),
        sa.Column("feature_key", sa.String(length=255), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"]),
        sa.PrimaryKeyConstraint("id", "recorded_at"),
        sa.UniqueConstraint("public_id", "recorded_at"),
        postgresql_partition_by="RANGE (recorded_at)",
    )
    op.create_index(
        "ix_usage_events_org_feature_recorded_at",
        "usage_events",
        ["organization_id", "feature_key", "recorded_at"],
        unique=False,
    )

    now = datetime.now(UTC)
    current_start, next_start = _month_bounds(now.year, now.month)
    _create_partition(current_start.year, current_start.month)
    _create_partition(next_start.year, next_start.month)


def downgrade() -> None:
    op.drop_index(
        "ix_usage_events_org_feature_recorded_at",
        table_name="usage_events",
    )
    op.drop_table("usage_events")
