"""Unit tests for idempotent usage batch ingestion."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.usage_event import UsageEvent
from billing_platform.services.usage import UsageEventIn, ingest_usage_batch


@pytest_asyncio.fixture
async def session(db_session: AsyncSession) -> AsyncSession:
    return db_session


@pytest_asyncio.fixture
async def org_id(session: AsyncSession) -> int:
    organization = Organization(name="Usage batch test organization")
    session.add(organization)
    await session.flush()
    return organization.id


async def count_usage(session: AsyncSession, org_id: int) -> int:
    count = await session.scalar(
        select(func.count()).select_from(UsageEvent).where(UsageEvent.organization_id == org_id)
    )
    assert count is not None
    return count


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_does_not_double_insert(session, org_id):
    e = UsageEventIn(feature_key="api_calls", quantity=1, idempotency_key="u-1")
    r1 = await ingest_usage_batch(session, organization_id=org_id, events=[e])
    r2 = await ingest_usage_batch(session, organization_id=org_id, events=[e])
    assert r1.accepted == 1 and r2.duplicates == 1
    assert r2.public_ids == r1.public_ids
    assert await count_usage(session, org_id) == 1


@pytest.mark.asyncio
async def test_duplicate_idempotency_key_across_partitions_returns_original_event(session, org_id):
    first_event = UsageEventIn(
        feature_key="api_calls",
        quantity=1,
        idempotency_key="cross-partition",
        recorded_at=datetime(2026, 2, 18, tzinfo=UTC),
    )
    retry_event = UsageEventIn(
        feature_key="api_calls",
        quantity=1,
        idempotency_key="cross-partition",
        recorded_at=datetime(2026, 6, 20, tzinfo=UTC),
    )

    first = await ingest_usage_batch(session, organization_id=org_id, events=[first_event])
    retry = await ingest_usage_batch(session, organization_id=org_id, events=[retry_event])

    assert first.accepted == 1
    assert retry.accepted == 0
    assert retry.duplicates == 1
    assert retry.public_ids == first.public_ids
    assert await count_usage(session, org_id) == 1
