"""Integration: Alembic upgrade on empty PostgreSQL."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import docker.errors
import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError
from testcontainers.community.postgres import PostgresContainer

PROJECT_ROOT = Path(__file__).resolve().parents[2]

_DOCKER_UNAVAILABLE_EXCEPTIONS = (
    docker.errors.DockerException,
    FileNotFoundError,
    ConnectionError,
    RequestsConnectionError,
)


@pytest.mark.integration
def test_alembic_upgrade_head_under_60s() -> None:
    try:
        with PostgresContainer("postgres:16") as postgres:
            host = postgres.get_container_host_ip()
            port = postgres.get_exposed_port(5432)
            user = postgres.username
            password = postgres.password
            dbname = postgres.dbname
            database_url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"

            env = os.environ.copy()
            env["DATABASE_URL"] = database_url

            start = time.monotonic()
            result = subprocess.run(
                ["uv", "run", "alembic", "upgrade", "head"],
                cwd=PROJECT_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            elapsed = time.monotonic() - start

            assert result.returncode == 0, result.stderr or result.stdout
            assert elapsed < 60.0, f"alembic upgrade took {elapsed:.1f}s (limit 60s)"
    except _DOCKER_UNAVAILABLE_EXCEPTIONS as exc:
        pytest.skip(f"Docker unavailable for PostgresContainer: {exc}")
