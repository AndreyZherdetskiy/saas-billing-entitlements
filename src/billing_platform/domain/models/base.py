"""Declarative base and shared mixins."""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, Identity, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from billing_platform.domain.ids import generate_uuidv7


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""


class DualIdMixin:
    """BIGINT identity PK + UUIDv7 public_id (ADR-010)."""

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(),
        primary_key=True,
    )
    public_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        unique=True,
        nullable=False,
        default=generate_uuidv7,
    )
