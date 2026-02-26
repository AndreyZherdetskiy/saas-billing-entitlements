"""Integration tests: multi-replica outbox relay without duplicate Kafka publish."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import Settings
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.outbox_relay.publisher import claim_outbox_batch, poll_and_publish
from billing_platform.services.outbox import enqueue

pytestmark = pytest.mark.integration

MESSAGE_COUNT = 30
BATCH_SIZE = 50


class _SpyProducer:
    """In-memory Kafka producer tracking message keys (outbox id)."""

    def __init__(self) -> None:
        self.published_keys: list[str] = []

    async def send_and_wait(self, topic: str, *, key: bytes, **kwargs: object) -> None:
        self.published_keys.append(key.decode("utf-8"))


async def _create_org(session: AsyncSession) -> Organization:
    org = Organization(name="Relay HA Test Org")
    session.add(org)
    await session.flush()
    return org


@pytest.mark.asyncio
async def test_concurrent_claim_outbox_batch_returns_disjoint_rows(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    max_attempts = 10

    async with session_factory() as session:
        org = await _create_org(session)
        for _ in range(MESSAGE_COUNT):
            await enqueue(
                session,
                event_type="subscription.activated",
                aggregate_type="subscription",
                aggregate_id=str(uuid.uuid4()),
                organization_id=org.id,
                partition_key=str(org.id),
                payload={"organization_id": org.id},
                idempotency_key=f"relay:ha:claim:{uuid.uuid4()}",
            )
        await session.commit()

    barrier = asyncio.Barrier(2)

    async def _claim_batch() -> list[int]:
        async with session_factory() as session, session.begin():
            rows = await claim_outbox_batch(
                session,
                limit=BATCH_SIZE,
                max_attempts=max_attempts,
            )
            ids = [row.id for row in rows]
            await barrier.wait()
            return ids

    claimed_a, claimed_b = await asyncio.gather(_claim_batch(), _claim_batch())
    overlap = set(claimed_a) & set(claimed_b)
    assert not overlap, f"concurrent claimers must not lock the same rows: {overlap}"
    assert len(claimed_a) + len(claimed_b) == MESSAGE_COUNT

    await engine.dispose()


@pytest.mark.asyncio
async def test_two_concurrent_relay_workers_publish_each_outbox_once(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    relay_settings = Settings(
        database_url=migrated_postgres_url,
        kafka_bootstrap_servers="unused:9092",
        outbox_batch_size=BATCH_SIZE,
    )
    producer = _SpyProducer()

    async with session_factory() as session:
        org = await _create_org(session)
        outbox_ids: list[int] = []
        for _ in range(MESSAGE_COUNT):
            outbox_id = await enqueue(
                session,
                event_type="subscription.activated",
                aggregate_type="subscription",
                aggregate_id=str(uuid.uuid4()),
                organization_id=org.id,
                partition_key=str(org.id),
                payload={"organization_id": org.id},
                idempotency_key=f"relay:ha:publish:{uuid.uuid4()}",
            )
            outbox_ids.append(outbox_id)
        await session.commit()

    published_a, published_b = await asyncio.gather(
        poll_and_publish(
            settings=relay_settings,
            session_factory=session_factory,
            producer=producer,
        ),
        poll_and_publish(
            settings=relay_settings,
            session_factory=session_factory,
            producer=producer,
        ),
    )

    assert published_a + published_b == MESSAGE_COUNT
    assert len(producer.published_keys) == MESSAGE_COUNT
    assert len(set(producer.published_keys)) == MESSAGE_COUNT
    assert set(producer.published_keys) == {str(outbox_id) for outbox_id in outbox_ids}

    async with session_factory() as session:
        pending = await session.scalar(
            select(func.count())
            .select_from(OutboxMessage)
            .where(OutboxMessage.published_at.is_(None))
        )
        assert pending == 0

    await engine.dispose()
