"""Dunning campaign and attempt ORM models (ADR-008)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.base import Base


class DunningCampaignStatus(str, enum.Enum):
    """Lifecycle status for a dunning campaign."""

    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    EXHAUSTED = "exhausted"


class DunningAttemptResult(str, enum.Enum):
    """Outcome of a dunning collection attempt."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class DunningCampaign(Base):
    """Recovery campaign for a subscription after payment failure."""

    __tablename__ = "dunning_campaigns"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=generate_uuidv7,
    )
    subscription_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("subscriptions.id"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=DunningCampaignStatus.ACTIVE.value,
    )
    grace_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    policy_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    attempts: Mapped[list[DunningAttempt]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
    )


class DunningAttempt(Base):
    """Scheduled collection attempt within a dunning campaign."""

    __tablename__ = "dunning_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=generate_uuidv7,
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("dunning_campaigns.id"),
        nullable=False,
        index=True,
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(String(32))
    external_charge_id: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    campaign: Mapped[DunningCampaign] = relationship(back_populates="attempts")
