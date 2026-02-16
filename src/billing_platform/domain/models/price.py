"""Price catalog ORM model."""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.base import Base


class PricingModel(StrEnum):
    FLAT = "flat"
    PER_UNIT = "per_unit"
    TIERED = "tiered"


class Price(Base):
    """Plan price row."""

    __tablename__ = "prices"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuidv7)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    unit_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    pricing_model: Mapped[str] = mapped_column(String(32), nullable=False)
    metered_feature_key: Mapped[str | None] = mapped_column(String(128))
    external_price_id: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
