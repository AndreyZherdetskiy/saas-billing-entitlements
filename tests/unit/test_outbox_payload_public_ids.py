"""Unit tests: outbox payloads must not leak internal BIGINT ids (dual-id policy)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.organization import Organization
from billing_platform.services.outbox import OutboxService, enqueue


async def _create_org(session: AsyncSession) -> Organization:
    org = Organization(name="Public Id Org")
    session.add(org)
    await session.flush()
    return org


@pytest.mark.asyncio
async def test_enqueue_strips_internal_bigint_ids_from_payload(db_session: AsyncSession) -> None:
    org = await _create_org(db_session)
    sub_public_id = str(uuid.uuid4())
    outbox_id = await enqueue(
        db_session,
        event_type="subscription.activated",
        aggregate_type="subscription",
        aggregate_id=sub_public_id,
        organization_id=org.id,
        partition_key=str(org.id),
        payload={
            "subscription_public_id": sub_public_id,
            "organization_public_id": str(org.public_id),
            "organization_id": org.id,
            "subscription_id": 999,
        },
        idempotency_key="test:payload:sanitize",
    )
    await db_session.commit()

    from billing_platform.domain.models.outbox_message import OutboxMessage

    row = await db_session.get(OutboxMessage, outbox_id)
    assert row is not None
    assert row.payload["organization_public_id"] == str(org.public_id)
    assert row.payload["subscription_public_id"] == sub_public_id
    assert "organization_id" not in row.payload
    assert "subscription_id" not in row.payload


@pytest.mark.asyncio
async def test_enqueue_no_inject_org_id_when_missing(db_session: AsyncSession) -> None:
    org = await _create_org(db_session)
    outbox_id = await OutboxService.enqueue(
        db_session,
        event_type="subscription.trial_started",
        aggregate_type="subscription",
        aggregate_id=str(uuid.uuid4()),
        organization_id=org.id,
        partition_key=str(org.id),
        payload={"organization_public_id": str(org.public_id)},
        idempotency_key="test:payload:no-inject",
    )
    await db_session.commit()

    from billing_platform.domain.models.outbox_message import OutboxMessage

    row = await db_session.get(OutboxMessage, outbox_id)
    assert row is not None
    assert "organization_id" not in row.payload
