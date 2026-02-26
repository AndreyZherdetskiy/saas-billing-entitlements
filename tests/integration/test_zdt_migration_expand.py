"""Integration: ZDT expand-only drill on hot table invoices (ADR-009).

Asserts that the expand migration adds a nullable column without requiring application
code changes, that concurrent reads on `invoices` survive the upgrade, and that the
expand step is reversible via downgrade.

PostgreSQL 11+ `ADD COLUMN ... NULL` does not rewrite the full table; it may take a
brief ACCESS EXCLUSIVE lock on relation metadata. This test does not assert zero lock
time — it documents the assumption and verifies liveness: concurrent SELECT completes
while upgrade runs.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from pathlib import Path

import docker.errors
import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from testcontainers.community.postgres import PostgresContainer

from billing_platform.domain.ids import generate_uuidv7

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ZDT_DRILL_REVISION = "20260216_0017"
PRE_DRILL_REVISION = "20260216_0016"

_DOCKER_UNAVAILABLE_EXCEPTIONS = (
    docker.errors.DockerException,
    FileNotFoundError,
    ConnectionError,
    RequestsConnectionError,
)


def _run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    return subprocess.run(
        ["uv", "run", "alembic", *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


async def _seed_invoice(session: AsyncSession) -> str:
    org_public_id = generate_uuidv7()
    invoice_public_id = generate_uuidv7()
    organization_id = await session.scalar(
        text(
            """
            INSERT INTO organizations (public_id, name)
            VALUES (:public_id, :name)
            RETURNING id
            """
        ),
        {"public_id": org_public_id, "name": "ZDT drill org"},
    )
    assert organization_id is not None
    await session.execute(
        text(
            """
            INSERT INTO invoices (
                public_id, organization_id, status, currency,
                period_start, period_end, idempotency_key
            )
            VALUES (
                :public_id, :organization_id, 'draft', 'USD',
                now(), now() + interval '1 month', :idempotency_key
            )
            """
        ),
        {
            "public_id": invoice_public_id,
            "organization_id": organization_id,
            "idempotency_key": "zdt-drill-seed",
        },
    )
    await session.commit()
    return str(invoice_public_id)


async def _wait_for_reader_ready(
    read_latencies: list[float],
    *,
    timeout: float = 5.0,
) -> None:
    """Poll until the background reader completes at least one SELECT."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if read_latencies:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("reader did not complete a poll before upgrade started")


async def _poll_invoices(
    database_url: str,
    stop_event: asyncio.Event,
    read_errors: list[str],
    read_latencies: list[float],
) -> None:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        while not stop_event.is_set():
            started = time.monotonic()
            try:
                async with session_factory() as session:
                    await session.scalar(text("SELECT count(*) FROM invoices"))
                read_latencies.append(time.monotonic() - started)
            except Exception as exc:  # noqa: BLE001 — collect for assertion
                read_errors.append(str(exc))
            await asyncio.sleep(0.05)
    finally:
        await engine.dispose()


@pytest.mark.integration
async def test_zdt_expand_adds_nullable_column_without_breaking_reads() -> None:
    try:
        with PostgresContainer("postgres:16") as postgres:
            host = postgres.get_container_host_ip()
            port = postgres.get_exposed_port(5432)
            database_url = (
                f"postgresql+asyncpg://{postgres.username}:{postgres.password}"
                f"@{host}:{port}/{postgres.dbname}"
            )

            bootstrap = _run_alembic(database_url, "upgrade", PRE_DRILL_REVISION)
            assert bootstrap.returncode == 0, bootstrap.stderr or bootstrap.stdout

            engine = create_async_engine(database_url, pool_pre_ping=True)
            session_factory = async_sessionmaker(
                engine, expire_on_commit=False, class_=AsyncSession
            )
            async with session_factory() as session:
                invoice_public_id = await _seed_invoice(session)
            await engine.dispose()

            stop_event = asyncio.Event()
            read_errors: list[str] = []
            read_latencies: list[float] = []
            reader = asyncio.create_task(
                _poll_invoices(database_url, stop_event, read_errors, read_latencies)
            )
            await _wait_for_reader_ready(read_latencies)

            loop = asyncio.get_running_loop()
            upgrade = await loop.run_in_executor(
                None,
                lambda: _run_alembic(database_url, "upgrade", ZDT_DRILL_REVISION),
            )
            stop_event.set()
            await reader

            assert upgrade.returncode == 0, upgrade.stderr or upgrade.stdout
            assert not read_errors, f"concurrent reads failed during upgrade: {read_errors}"
            assert read_latencies, "expected at least one concurrent read during upgrade"
            assert max(read_latencies) < 5.0, (
                "concurrent SELECT blocked too long during expand; "
                "check lock strategy per docs/runbooks/migration-zdt-usage.md"
            )

            verify_engine = create_async_engine(database_url, pool_pre_ping=True)
            verify_factory = async_sessionmaker(
                verify_engine, expire_on_commit=False, class_=AsyncSession
            )
            async with verify_factory() as session:
                column = await session.execute(
                    text(
                        """
                        SELECT column_name, is_nullable
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'invoices'
                          AND column_name = 'zdt_drill_marker'
                        """
                    )
                )
                row = column.one()
                assert row.column_name == "zdt_drill_marker"
                assert row.is_nullable == "YES"

                marker = await session.scalar(
                    text("SELECT zdt_drill_marker FROM invoices WHERE public_id = :public_id"),
                    {"public_id": invoice_public_id},
                )
                assert marker is None
            await verify_engine.dispose()

            downgrade = _run_alembic(database_url, "downgrade", PRE_DRILL_REVISION)
            assert downgrade.returncode == 0, downgrade.stderr or downgrade.stdout

            after_engine = create_async_engine(database_url, pool_pre_ping=True)
            after_factory = async_sessionmaker(
                after_engine, expire_on_commit=False, class_=AsyncSession
            )
            async with after_factory() as session:
                remaining = await session.scalar(
                    text(
                        """
                        SELECT count(*)
                        FROM information_schema.columns
                        WHERE table_schema = 'public'
                          AND table_name = 'invoices'
                          AND column_name = 'zdt_drill_marker'
                        """
                    )
                )
                assert remaining == 0
            await after_engine.dispose()
    except _DOCKER_UNAVAILABLE_EXCEPTIONS as exc:
        pytest.skip(f"Docker unavailable for PostgresContainer: {exc}")


@pytest.mark.integration
async def test_zdt_drill_column_present_at_head_not_in_orm(migrated_postgres_url: str) -> None:
    """Head schema includes drill column in DB; app ORM intentionally omits it (expand-only)."""
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        column_name = await session.scalar(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'invoices'
                  AND column_name = 'zdt_drill_marker'
                """
            )
        )
        assert column_name == "zdt_drill_marker"
    await engine.dispose()
