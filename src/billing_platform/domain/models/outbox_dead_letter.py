"""Dead-letter storage for poison outbox rows (ADR-001)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.models.base import Base


class OutboxDeadLetter(Base):
    """Snapshot of an outbox row moved after max publish attempts."""

    __tablename__ = "outbox_dead_letters"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    outbox_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    partition_key: Mapped[str] = mapped_column(String(128), nullable=False)
    publish_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    moved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
