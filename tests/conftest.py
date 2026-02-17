"""Shared pytest fixtures."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from pathlib import Path

import docker.errors
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from requests.exceptions import ConnectionError as RequestsConnectionError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.redis import RedisContainer

from billing_platform.config import get_settings
from billing_platform.db import close_db_engine, get_read_session, get_session, reset_db_singletons
from billing_platform.main import create_app
from billing_platform.services.hotpath_cache import clear_hotpath_caches
from billing_platform.services.usage_partitions import (
    ensure_current_and_next_partitions,
    month_bounds,
)
from tests.docker_engine import (
    REDIS_IMAGE,
    docker_cli_available,
    docker_sdk_likely_available,
    postgres_via_docker_cli,
    redis_via_docker_cli,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_DOCKER_UNAVAILABLE_EXCEPTIONS = (
    docker.errors.DockerException,
    FileNotFoundError,
    ConnectionError,
    RequestsConnectionError,
)


@pytest.fixture(autouse=True)
def _clear_hotpath_caches() -> Iterator[None]:
    """Drop process-local auth/snapshot/org L1 so tests do not leak across cases."""
    clear_hotpath_caches()
    yield
    clear_hotpath_caches()


@pytest.fixture(autouse=True)
def disable_otel_for_tests(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Disable OTel SDK in most tests; observability tests opt in via otel_enabled."""
    if "otel_enabled" in request.fixturenames:
        return
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    get_settings.cache_clear()


def _run_alembic_upgrade(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    result = subprocess.run(
        ["uv", "run", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)


_USAGE_PARTITION_NAME = re.compile(r"^usage_events_\d{4}_\d{2}$")

_TRUNCATE_PUBLIC_SQL = """
DO $outer$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT c.relname AS tablename
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind IN ('r', 'p')
          AND NOT c.relispartition
          AND c.relname <> 'alembic_version'
    LOOP
        EXECUTE 'TRUNCATE TABLE ' || quote_ident(r.tablename)
            || ' RESTART IDENTITY CASCADE';
    END LOOP;
END
$outer$;
"""


def _usage_events_alembic_partition_names() -> frozenset[str]:
    """Partition names created by alembic 0010 for the current clock."""
    current_start, next_start = month_bounds(datetime.now(UTC))
    return frozenset(
        {
            f"usage_events_{current_start:%Y_%m}",
            f"usage_events_{next_start:%Y_%m}",
        }
    )


def _drop_extra_usage_partitions_sql(keep: frozenset[str]) -> str:
    """DROP TABLE extra RANGE partitions; TRUNCATE does not remove them (PG 16)."""
    if len(keep) != 2 or any(not _USAGE_PARTITION_NAME.fullmatch(name) for name in keep):
        raise ValueError("keep must be two usage_events_YYYY_MM names")
    keep_list = ", ".join(f"'{name}'" for name in sorted(keep))
    return f"""
DO $outer$
DECLARE
    rec record;
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_class AS parent
        JOIN pg_namespace AS nsp ON nsp.oid = parent.relnamespace
        WHERE nsp.nspname = 'public'
          AND parent.relname = 'usage_events'
          AND parent.relkind = 'p'
    ) THEN
        RETURN;
    END IF;
    FOR rec IN
        SELECT child.relname AS partition_name
        FROM pg_inherits AS inheritance
        JOIN pg_class AS child ON child.oid = inheritance.inhrelid
        JOIN pg_class AS parent ON parent.oid = inheritance.inhparent
        JOIN pg_namespace AS nsp ON nsp.oid = child.relnamespace
        WHERE parent.relname = 'usage_events'
          AND nsp.nspname = 'public'
          AND child.relname <> ALL (ARRAY[{keep_list}]::text[])
    LOOP
        EXECUTE format('DROP TABLE %I', rec.partition_name);
    END LOOP;
END
$outer$;
"""


def _reset_migrated_postgres(database_url: str) -> None:
    """Empty public tables and restore usage_events to current+next partitions."""

    async def _run() -> None:
        engine = create_async_engine(database_url, pool_pre_ping=True)
        session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        try:
            async with engine.begin() as conn:
                await conn.execute(text(_TRUNCATE_PUBLIC_SQL))
                await conn.execute(
                    text(_drop_extra_usage_partitions_sql(_usage_events_alembic_partition_names()))
                )
            async with session_factory.begin() as session:
                await ensure_current_and_next_partitions(session)
        finally:
            await engine.dispose()

    asyncio.run(_run())


@pytest.fixture(scope="session")
def _session_postgres_url() -> Iterator[str]:
    """One Postgres + Alembic head for the whole pytest session."""
    if docker_sdk_likely_available():
        try:
            with PostgresContainer("postgres:16") as postgres:
                host = postgres.get_container_host_ip()
                port = postgres.get_exposed_port(5432)
                database_url = (
                    f"postgresql+asyncpg://{postgres.username}:{postgres.password}"
                    f"@{host}:{port}/{postgres.dbname}"
                )
                _run_alembic_upgrade(database_url)
                yield database_url
                return
        except _DOCKER_UNAVAILABLE_EXCEPTIONS:
            pass

    if not docker_cli_available():
        pytest.skip("Docker unavailable for Postgres (SDK socket and CLI)")

    with postgres_via_docker_cli() as (host, port, user, password):
        database_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/test"
        _run_alembic_upgrade(database_url)
        yield database_url


@pytest.fixture
def migrated_postgres_url(
    _session_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """Per-test DATABASE_URL: truncate data, restore usage_events to current+next.

    Override compose `.env` hostnames (`postgres`) so host pytest does not
    resolve Docker DNS names. Environment variables take priority over dotenv
    (pydantic-settings).
    """
    monkeypatch.setenv("DATABASE_URL", _session_postgres_url)
    get_settings.cache_clear()
    _reset_migrated_postgres(_session_postgres_url)
    yield _session_postgres_url
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def _session_redis_url() -> Iterator[str]:
    """One Redis for the whole pytest session."""
    if docker_sdk_likely_available():
        try:
            with RedisContainer(REDIS_IMAGE) as redis_container:
                host = redis_container.get_container_host_ip()
                port = redis_container.get_exposed_port(6379)
                yield f"redis://{host}:{port}/0"
                return
        except _DOCKER_UNAVAILABLE_EXCEPTIONS:
            pass

    if not docker_cli_available():
        pytest.skip("Docker unavailable for Redis (SDK socket and CLI)")

    with redis_via_docker_cli() as (host, port):
        yield f"redis://{host}:{port}/0"


@pytest_asyncio.fixture
async def redis_client(
    _session_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Redis]:
    """Yield a Redis client; FLUSHDB around each test."""
    monkeypatch.setenv("REDIS_URL", _session_redis_url)
    get_settings.cache_clear()
    client = Redis.from_url(_session_redis_url, decode_responses=True)
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture
async def db_session(migrated_postgres_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest_asyncio.fixture
async def api_client(
    migrated_postgres_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE", "0")
    get_settings.cache_clear()
    reset_db_singletons()

    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_read_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    app.dependency_overrides.clear()
    get_settings.cache_clear()
    await close_db_engine()
    reset_db_singletons()
    await engine.dispose()
