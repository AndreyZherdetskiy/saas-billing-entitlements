"""Unit tests: outbox relay poison path → dead letters + billing.dlq (Gate D)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import Settings
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.outbox_dead_letter import OutboxDeadLetter
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.outbox_relay.publisher import DLQ_TOPIC, poll_and_publish
from billing_platform.services.outbox import enqueue


class _SelectiveFailingProducer:
    """Fail primary topic publish; succeed on billing.dlq for DLQ verification."""

    def __init__(self) -> None:
        self.sent_topics: list[str] = []

    async def send_and_wait(self, topic: str, **kwargs: object) -> None:
        self.sent_topics.append(topic)
        if topic != DLQ_TOPIC:
            raise RuntimeError("kafka publish failed")


async def _create_org(session: AsyncSession) -> Organization:
    org = Organization(name="DLQ Test Org")
    session.add(org)
    await session.flush()
    return org


@pytest.mark.asyncio
async def test_publish_failure_at_max_attempts_moves_to_dead_letter_and_dlq(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    relay_settings = Settings(
        database_url=migrated_postgres_url,
        kafka_bootstrap_servers="unused:9092",
        outbox_max_attempts=1,
    )
    producer = _SelectiveFailingProducer()

    async with session_factory() as session:
        org = await _create_org(session)
        outbox_id = await enqueue(
            session,
            event_type="subscription.activated",
            aggregate_type="subscription",
            aggregate_id=str(uuid.uuid4()),
            organization_id=org.id,
            partition_key=str(org.id),
            payload={"organization_id": org.id},
            idempotency_key=f"dlq:test:kafka-fail:{uuid.uuid4()}",
        )
        await session.commit()

    published = await poll_and_publish(
        batch_size=10,
        settings=relay_settings,
        session_factory=session_factory,
        producer=producer,
    )
    assert published == 0
    assert DLQ_TOPIC in producer.sent_topics

    async with session_factory() as session:
        row = await session.get(OutboxMessage, outbox_id)
        assert row is not None
        assert row.published_at is not None
        assert row.publish_attempts >= relay_settings.outbox_max_attempts

        dead_count = await session.scalar(
            select(func.count())
            .select_from(OutboxDeadLetter)
            .where(OutboxDeadLetter.outbox_message_id == outbox_id)
        )
        assert dead_count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_org_not_found_at_max_attempts_moves_to_dead_letter_and_dlq(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    relay_settings = Settings(
        database_url=migrated_postgres_url,
        kafka_bootstrap_servers="unused:9092",
        outbox_max_attempts=1,
    )
    producer = _SelectiveFailingProducer()
    missing_partition_key = "999999999"

    async with session_factory() as session:
        outbox_id = await enqueue(
            session,
            event_type="subscription.activated",
            aggregate_type="subscription",
            aggregate_id=str(uuid.uuid4()),
            organization_id=1,
            partition_key=missing_partition_key,
            payload={"organization_id": 1},
            idempotency_key=f"dlq:test:org-missing:{uuid.uuid4()}",
        )
        await session.commit()

    published = await poll_and_publish(
        batch_size=10,
        settings=relay_settings,
        session_factory=session_factory,
        producer=producer,
    )
    assert published == 0
    assert DLQ_TOPIC in producer.sent_topics

    async with session_factory() as session:
        row = await session.get(OutboxMessage, outbox_id)
        assert row is not None
        assert row.published_at is not None
        assert "organization not found" in (row.last_error or "")

        dead_count = await session.scalar(
            select(func.count())
            .select_from(OutboxDeadLetter)
            .where(OutboxDeadLetter.outbox_message_id == outbox_id)
        )
        assert dead_count == 1

    await engine.dispose()
