"""Unit tests: transactional outbox enqueue."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.outbox_relay.publisher import SUBSCRIPTION_EVENT_TYPES, resolve_topic
from billing_platform.services.outbox import OutboxService, enqueue


async def _create_org(session: AsyncSession) -> Organization:
    org = Organization(name="Outbox Test Org")
    session.add(org)
    await session.flush()
    return org


@pytest.mark.asyncio
async def test_enqueue_returns_outbox_id(db_session: AsyncSession) -> None:
    org = await _create_org(db_session)
    outbox_id = await enqueue(
        db_session,
        event_type="subscription.activated",
        aggregate_type="subscription",
        aggregate_id=str(uuid.uuid4()),
        organization_id=org.id,
        partition_key=str(org.id),
        payload={"plan_key": "pro"},
        idempotency_key="test:enqueue:activated",
    )
    await db_session.commit()

    assert outbox_id > 0
    row = await db_session.get(OutboxMessage, outbox_id)
    assert row is not None
    assert row.event_type == "subscription.activated"
    assert row.published_at is None


@pytest.mark.asyncio
async def test_enqueue_idempotency_conflict_returns_zero(db_session: AsyncSession) -> None:
    org = await _create_org(db_session)
    kwargs = {
        "event_type": "subscription.activated",
        "aggregate_type": "subscription",
        "aggregate_id": str(uuid.uuid4()),
        "organization_id": org.id,
        "partition_key": str(org.id),
        "payload": {"plan_key": "pro"},
        "idempotency_key": "test:enqueue:dup",
    }
    first = await enqueue(db_session, **kwargs)
    second = await OutboxService.enqueue(db_session, **kwargs)
    await db_session.commit()

    assert first > 0
    assert second == 0
    count = await db_session.scalar(
        select(func.count())
        .select_from(OutboxMessage)
        .where(OutboxMessage.idempotency_key == "test:enqueue:dup")
    )
    assert count == 1


@pytest.mark.parametrize(
    "event_type",
    sorted(SUBSCRIPTION_EVENT_TYPES),
)
def test_subscription_event_types_map_to_subscription_topic(event_type: str) -> None:
    assert resolve_topic(event_type) == "billing.subscription.events"


def test_ledger_entry_posted_maps_to_ledger_topic() -> None:
    assert resolve_topic("ledger.entry_posted") == "billing.ledger.events"
