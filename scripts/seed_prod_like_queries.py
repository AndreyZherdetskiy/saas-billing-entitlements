"""Query helpers for prod-like seed idempotency tests."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.organization import Organization


async def count_pl_main_orgs(session: AsyncSession) -> int:
    """Count main prod-like orgs (pl_org_* idempotency keys)."""
    result = await session.scalar(
        select(func.count())
        .select_from(Organization)
        .where(Organization.idempotency_key.like("pl_org_%"))
    )
    return int(result or 0)
