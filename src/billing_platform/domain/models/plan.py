"""Plan catalog ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.base import Base


class BillingInterval(StrEnum):
    MONTH = "month"
    YEAR = "year"


class Plan(Base):
    """Subscription plan (versioned; draft until published)."""

    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("product_id", "key", "version"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuidv7)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.id"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(128), nullable=False)
    billing_interval: Mapped[str] = mapped_column(String(16), nullable=False)
    trial_days: Mapped[int | None] = mapped_column(Integer)
    grace_period_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default="7")
    dunning_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    entitlement_policy: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
