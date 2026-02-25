"""Integration tests: outbox DLQ replay script ( Gate D)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import Settings
from billing_platform.domain.models.ledger import LedgerEntry
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.outbox_dead_letter import OutboxDeadLetter
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.outbox_relay.dlq_replay import ReplayStatus, replay_dead_letters
from billing_platform.outbox_relay.publisher import DLQ_TOPIC, poll_and_publish
from billing_platform.services.outbox import enqueue

pytestmark = pytest.mark.integration


class _SelectiveFailingProducer:
    def __init__(self) -> None:
        self.sent_topics: list[str] = []

    async def send_and_wait(self, topic: str, **kwargs: object) -> None:
        self.sent_topics.append(topic)
        if topic != DLQ_TOPIC:
            raise RuntimeError("kafka publish failed")


async def _create_org(session: AsyncSession) -> Organization:
    org = Organization(name="DLQ Replay Test Org")
    session.add(org)
    await session.flush()
    return org


async def _poison_outbox(
    session_factory: async_sessionmaker[AsyncSession],
    migrated_postgres_url: str,
) -> tuple[int, int]:
    """Enqueue one row, fail relay → return (outbox_id, dlq_id)."""
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
            idempotency_key=f"dlq:replay:test:{uuid.uuid4()}",
        )
        await session.commit()

    await poll_and_publish(
        batch_size=10,
        settings=relay_settings,
        session_factory=session_factory,
        producer=producer,
    )

    async with session_factory() as session:
        dlq_id = await session.scalar(
            select(OutboxDeadLetter.id).where(OutboxDeadLetter.outbox_message_id == outbox_id)
        )
        assert dlq_id is not None
        return outbox_id, int(dlq_id)


@pytest.mark.asyncio
async def test_replay_dead_letter_resets_outbox_to_publishable(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    outbox_id, dlq_id = await _poison_outbox(session_factory, migrated_postgres_url)

    async with session_factory() as session:
        ledger_before = await session.scalar(select(func.count()).select_from(LedgerEntry))

    results = await replay_dead_letters(session_factory, [dlq_id])
    assert len(results) == 1
    assert results[0].dlq_id == dlq_id
    assert results[0].status == ReplayStatus.replayed
    assert results[0].outbox_message_id == outbox_id

    async with session_factory() as session:
        row = await session.get(OutboxMessage, outbox_id)
        assert row is not None
        assert row.published_at is None
        assert row.publish_attempts == 0
        assert row.last_error is None

        dlq = await session.get(OutboxDeadLetter, dlq_id)
        assert dlq is not None
        assert dlq.replayed_at is not None

        ledger_after = await session.scalar(select(func.count()).select_from(LedgerEntry))
        assert ledger_after == ledger_before

    await engine.dispose()


@pytest.mark.asyncio
async def test_replay_same_dlq_id_twice_is_idempotent(
    migrated_postgres_url: str,
) -> None:
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    outbox_id, dlq_id = await _poison_outbox(session_factory, migrated_postgres_url)

    first = await replay_dead_letters(session_factory, [dlq_id])
    assert first[0].status == ReplayStatus.replayed

    second = await replay_dead_letters(session_factory, [dlq_id])
    assert len(second) == 1
    assert second[0].status == ReplayStatus.already_replayed

    async with session_factory() as session:
        pending = await session.scalar(
            select(func.count())
            .select_from(OutboxMessage)
            .where(
                OutboxMessage.id == outbox_id,
                OutboxMessage.published_at.is_(None),
            )
        )
        assert pending == 1

    await engine.dispose()
