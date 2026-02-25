"""Partitioned usage event ORM model (ADR-011)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Identity,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.base import Base, DualIdMixin


class UsageEvent(Base, DualIdMixin):
    """Immutable usage event routed by ``recorded_at`` to a monthly partition."""

    __tablename__ = "usage_events"
    __table_args__ = (
        PrimaryKeyConstraint("id", "recorded_at"),
        UniqueConstraint("public_id", "recorded_at"),
        {"postgresql_partition_by": "RANGE (recorded_at)"},
    )

    # Partitioned-table uniqueness must include the partition key, so these
    # columns intentionally override DualIdMixin's single-column constraints.
    id: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False)
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        default=generate_uuidv7,
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id"),
        nullable=False,
    )
    subscription_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("subscriptions.id"),
        nullable=True,
    )
    feature_key: Mapped[str] = mapped_column(String(255), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default="{}",
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
