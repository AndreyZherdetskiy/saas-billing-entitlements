"""Integration tests: outbox relay publishes to Kafka."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator

import docker.errors
import pytest
import pytest_asyncio
from aiokafka import AIOKafkaConsumer
from requests.exceptions import ConnectionError as RequestsConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.kafka import KafkaContainer

from billing_platform.config import Settings
from billing_platform.domain.models.organization import Organization
from billing_platform.outbox_relay.publisher import poll_and_publish, resolve_topic
from billing_platform.services.outbox import enqueue

pytestmark = pytest.mark.integration

_DOCKER_UNAVAILABLE_EXCEPTIONS = (
    docker.errors.DockerException,
    FileNotFoundError,
    ConnectionError,
    RequestsConnectionError,
)

SUBSCRIPTION_EVENTS = (
    "subscription.trial_started",
    "subscription.activated",
    "subscription.payment_failed",
    "subscription.past_due",
    "subscription.canceled",
)


@pytest_asyncio.fixture
async def kafka_bootstrap() -> AsyncIterator[str]:
    try:
        with KafkaContainer() as kafka:
            yield kafka.get_bootstrap_server()
    except _DOCKER_UNAVAILABLE_EXCEPTIONS as exc:
        pytest.skip(f"Docker unavailable for KafkaContainer: {exc}")


@pytest_asyncio.fixture
async def relay_settings(
    migrated_postgres_url: str,
    kafka_bootstrap: str,
) -> Settings:
    return Settings(
        database_url=migrated_postgres_url,
        kafka_bootstrap_servers=kafka_bootstrap,
    )


async def _create_org(session: AsyncSession) -> Organization:
    org = Organization(name="Relay Test Org")
    session.add(org)
    await session.flush()
    return org


async def _consume_json_messages(
    bootstrap: str,
    *,
    expected_count: int,
) -> list[dict[str, object]]:
    consumer = AIOKafkaConsumer(
        "billing.subscription.events",
        bootstrap_servers=bootstrap,
        auto_offset_reset="earliest",
        group_id=f"test-{uuid.uuid4()}",
    )
    await consumer.start()
    messages: list[dict[str, object]] = []
    try:
        while len(messages) < expected_count:
            record = await consumer.getone()
            raw = record.value.decode("utf-8")
            if not raw.startswith("{"):
                continue
            messages.append(json.loads(raw))
    finally:
        await consumer.stop()
    return messages


@pytest.mark.asyncio
async def test_relay_publishes_envelope_to_kafka(
    db_session: AsyncSession,
    relay_settings: Settings,
    kafka_bootstrap: str,
) -> None:
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
            "plan_key": "pro",
        },
        idempotency_key=f"relay:test:activated:{uuid.uuid4()}",
    )
    await db_session.commit()

    n = await poll_and_publish(settings=relay_settings)
    assert n == 1

    consumer = AIOKafkaConsumer(
        "billing.subscription.events",
        bootstrap_servers=kafka_bootstrap,
        auto_offset_reset="earliest",
        group_id=f"test-single-{uuid.uuid4()}",
    )
    await consumer.start()
    try:
        record = await consumer.getone()
        body = json.loads(record.value.decode("utf-8"))
    finally:
        await consumer.stop()

    assert body["schema_version"] == 1
    assert body["event_type"] == "subscription.activated"
    assert body["organization_id"] == str(org.public_id)
    assert body["payload"]["subscription_public_id"] == sub_public_id
    assert record.key.decode("utf-8") == str(outbox_id)


@pytest.mark.asyncio
async def test_relay_publishes_all_five_subscription_event_types(
    migrated_postgres_url: str,
    kafka_bootstrap: str,
    relay_settings: Settings,
) -> None:
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        org = await _create_org(session)
        for event_type in SUBSCRIPTION_EVENTS:
            await enqueue(
                session,
                event_type=event_type,
                aggregate_type="subscription",
                aggregate_id=str(uuid.uuid4()),
                organization_id=org.id,
                partition_key=str(org.id),
                payload={"organization_public_id": str(org.public_id), "event": event_type},
                idempotency_key=f"relay:test:{event_type}:{uuid.uuid4()}",
            )
        await session.commit()

    published = await poll_and_publish(batch_size=10, settings=relay_settings)
    assert published == len(SUBSCRIPTION_EVENTS)

    bodies = await _consume_json_messages(
        kafka_bootstrap,
        expected_count=len(SUBSCRIPTION_EVENTS),
    )
    observed_types = {str(body["event_type"]) for body in bodies}
    assert observed_types == set(SUBSCRIPTION_EVENTS)
    for body in bodies:
        assert body["schema_version"] == 1
        assert resolve_topic(str(body["event_type"])) == "billing.subscription.events"

    await engine.dispose()
