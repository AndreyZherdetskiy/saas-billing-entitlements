"""Hourly usage aggregate ORM model."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.models.base import Base, DualIdMixin


class UsageAggregate(Base, DualIdMixin):
    """Rolled-up usage quantity for one organization, feature, and hour bucket."""

    __tablename__ = "usage_aggregates_hourly"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "feature_key",
            "hour_start",
            name="uq_usage_aggregates_hourly_org_feature_hour",
        ),
    )

    organization_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id"),
        nullable=False,
    )
    feature_key: Mapped[str] = mapped_column(String(255), nullable=False)
    hour_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
