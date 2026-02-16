"""Expand-only: catalog tables (ADR-010 UUIDv7 PK)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260216_0004"
down_revision: str | None = "20260216_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.String(length=1024), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    op.create_table(
        "features",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("feature_type", sa.String(length=32), nullable=False),
        sa.Column("default_limit", sa.Integer(), nullable=True),
        sa.Column("reset_interval", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )

    op.create_table(
        "plans",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("billing_interval", sa.String(length=16), nullable=False),
        sa.Column("trial_days", sa.Integer(), nullable=True),
        sa.Column("grace_period_days", sa.Integer(), server_default=sa.text("7"), nullable=False),
        sa.Column(
            "dunning_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "entitlement_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", "key", "version"),
    )

    op.create_table(
        "prices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column(
            "currency", sa.String(length=3), server_default=sa.text("'USD'"), nullable=False
        ),
        sa.Column("unit_amount_cents", sa.Integer(), nullable=False),
        sa.Column("pricing_model", sa.String(length=32), nullable=False),
        sa.Column("metered_feature_key", sa.String(length=128), nullable=True),
        sa.Column("external_price_id", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prices_plan_id", "prices", ["plan_id"], unique=False)

    op.create_table(
        "plan_features",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("feature_id", sa.Uuid(), nullable=False),
        sa.Column("limit_value", sa.Integer(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "enforcement_mode",
            sa.String(length=16),
            server_default=sa.text("'hard'"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["feature_id"], ["features.id"]),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("plan_id", "feature_id"),
    )
    op.create_index("ix_plan_features_plan_id", "plan_features", ["plan_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_plan_features_plan_id", table_name="plan_features")
    op.drop_table("plan_features")
    op.drop_index("ix_prices_plan_id", table_name="prices")
    op.drop_table("prices")
    op.drop_table("plans")
    op.drop_table("features")
    op.drop_table("products")
