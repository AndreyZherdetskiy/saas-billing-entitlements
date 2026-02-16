"""SHA-256 unique key_hash lookup (ADR-015); delete unverifiable bcrypt rows."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260216_0019"
down_revision: str | None = "20260216_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("DELETE FROM api_keys"))
    op.alter_column(
        "api_keys",
        "key_hash",
        type_=sa.String(length=64),
        existing_type=sa.String(length=255),
        existing_nullable=False,
    )
    op.create_index("uq_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_api_keys_key_hash", table_name="api_keys")
    op.alter_column(
        "api_keys",
        "key_hash",
        type_=sa.String(length=255),
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
