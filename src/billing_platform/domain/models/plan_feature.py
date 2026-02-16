"""Plan-feature association ORM model."""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.base import Base


class EnforcementMode(StrEnum):
    HARD = "hard"
    SOFT = "soft"
    DEGRADED = "degraded"


class PlanFeature(Base):
    """Plan-to-feature binding with limits and enforcement."""

    __tablename__ = "plan_features"
    __table_args__ = (UniqueConstraint("plan_id", "feature_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=generate_uuidv7)
    plan_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("plans.id"), nullable=False)
    feature_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("features.id"), nullable=False)
    limit_value: Mapped[int | None] = mapped_column(Integer)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    enforcement_mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="hard",
    )
