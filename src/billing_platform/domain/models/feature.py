"""Feature catalog ORM model."""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.base import Base


class FeatureType(StrEnum):
    BOOLEAN = "boolean"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    SEAT = "seat"


class ResetInterval(StrEnum):
    HOUR = "hour"
    DAY = "day"
    MONTH = "month"
    BILLING_PERIOD = "billing_period"


class Feature(Base):
    """Entitlement feature definition."""

    __tablename__ = "features"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuidv7)
    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    feature_type: Mapped[str] = mapped_column(String(32), nullable=False)
    default_limit: Mapped[int | None] = mapped_column(Integer)
    reset_interval: Mapped[str | None] = mapped_column(String(32))
