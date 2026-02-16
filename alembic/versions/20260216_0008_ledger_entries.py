"""Append-only ledger_entries (ADR-006); prod INSERT-only — docs/adr/006-append-only-ledger.md."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260216_0008"
down_revision: str | None = "20260216_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("public_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.BigInteger(), nullable=False),
        sa.Column("subscription_id", sa.BigInteger(), nullable=True),
        sa.Column("invoice_id", sa.BigInteger(), nullable=True),
        sa.Column("entry_type", sa.String(length=64), nullable=False),
        sa.Column("amount_cents", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("reverses_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("correlation_id", sa.String(length=255), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
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
        sa.ForeignKeyConstraint(["reverses_entry_id"], ["ledger_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("public_id"),
    )
    op.create_index(
        "ix_ledger_entries_organization_id_created_at",
        "ledger_entries",
        ["organization_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ledger_entries_organization_id_created_at",
        table_name="ledger_entries",
    )
    op.drop_table("ledger_entries")
