"""Integration tests: PgBouncer service and pool sizing."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from billing_platform.config import Settings, get_settings
from tests.cli_tools import require_docker_compose

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "deploy" / "compose" / "docker-compose.yml"
PGBOUNCER_INI = REPO_ROOT / "deploy" / "compose" / "pgbouncer" / "pgbouncer.ini"
HELM_VALUES = REPO_ROOT / "deploy" / "helm" / "billing-platform" / "values.yaml"


def _rendered_compose_config(*profiles: str) -> dict:
    require_docker_compose()
    env_file = REPO_ROOT / ".env.example"
    cmd = [
        "docker",
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(COMPOSE_FILE),
    ]
    for profile in profiles:
        cmd.extend(["--profile", profile])
    cmd.append("config")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
        cwd=REPO_ROOT,
    )
    return yaml.safe_load(result.stdout)


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_pgbouncer_service_defined_with_stage3_profile() -> None:
    config = _rendered_compose_config("pgbouncer")
    assert "pgbouncer" in config["services"]

    svc = config["services"]["pgbouncer"]
    profiles = svc.get("profiles", [])
    assert "pgbouncer" in profiles or "stage3" in profiles

    published_ports = svc.get("ports", [])
    assert any("6432" in str(p) for p in published_ports)

    depends_on = svc.get("depends_on", {})
    assert "postgres" in depends_on
    assert depends_on["postgres"]["condition"] == "service_healthy"


def test_pgbouncer_ini_documents_pool_sizes() -> None:
    ini = PGBOUNCER_INI.read_text()
    assert "default_pool_size" in ini
    assert "max_client_conn" in ini
    assert "pool_mode" in ini
    assert "host=postgres" in ini


def test_helm_values_expose_pgbouncer_stub() -> None:
    values = yaml.safe_load(HELM_VALUES.read_text())
    assert "pgbouncer" in values
    pgbouncer = values["pgbouncer"]
    assert pgbouncer.get("enabled") is False
    assert "defaultPoolSize" in pgbouncer
    assert "maxClientConn" in pgbouncer


def test_settings_expose_sqlalchemy_pool_defaults() -> None:
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)
    assert settings.database_pool_size == 20
    assert settings.database_max_overflow == 10


def test_settings_database_pool_size_from_env() -> None:
    with patch.dict(
        os.environ,
        {"DATABASE_POOL_SIZE": "10", "DATABASE_MAX_OVERFLOW": "5"},
        clear=True,
    ):
        settings = Settings(_env_file=None)
    assert settings.database_pool_size == 10
    assert settings.database_max_overflow == 5
