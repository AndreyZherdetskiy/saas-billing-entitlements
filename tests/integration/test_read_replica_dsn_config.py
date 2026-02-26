"""Integration tests: read-replica DSN settings."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from billing_platform.config import Settings, get_settings

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_settings_exposes_database_read_url_optional_by_default() -> None:
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)
    assert hasattr(settings, "database_read_url")
    assert settings.database_read_url is None


def test_settings_database_read_url_from_env() -> None:
    dsn = "postgresql+asyncpg://billing:billing@postgres-replica:5432/billing"
    with patch.dict(os.environ, {"DATABASE_READ_URL": dsn}, clear=True):
        settings = Settings(_env_file=None)
    assert settings.database_read_url == dsn


def test_settings_empty_database_read_url_becomes_none() -> None:
    with patch.dict(os.environ, {"DATABASE_READ_URL": ""}, clear=True):
        settings = Settings(_env_file=None)
    assert settings.database_read_url is None


def test_settings_exposes_replica_lag_threshold_seconds() -> None:
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(_env_file=None)
    assert hasattr(settings, "replica_lag_threshold_seconds")
    assert settings.replica_lag_threshold_seconds == 30


def test_settings_replica_lag_threshold_from_env() -> None:
    with patch.dict(os.environ, {"REPLICA_LAG_THRESHOLD_SECONDS": "60"}, clear=True):
        settings = Settings(_env_file=None)
    assert settings.replica_lag_threshold_seconds == 60
