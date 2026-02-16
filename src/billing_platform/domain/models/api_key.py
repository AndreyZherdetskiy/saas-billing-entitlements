"""API key ORM model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.base import Base


class ApiKeyRole(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    REVOPS_READ = "revops_read"
    PRODUCT_SERVICE = "product_service"
    WEBHOOK_INGEST = "webhook_ingest"
    SUPPORT_READ = "support_read"
    DUNNING_OPERATOR = "dunning_operator"


class ApiKey(Base):
    """Hashed API key scoped to an organization or platform-wide."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=generate_uuidv7,
    )
    organization_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("organizations.id"),
        nullable=True,
    )
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
