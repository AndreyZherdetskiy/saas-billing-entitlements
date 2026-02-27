"""Unit tests: Settings env edge cases (empty bools from compose .env)."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from billing_platform.config import Settings, get_settings


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_empty_otel_sdk_disabled_defaults_to_false() -> None:
    with patch.dict(os.environ, {"OTEL_SDK_DISABLED": ""}, clear=True):
        settings = Settings(_env_file=None)
    assert settings.otel_sdk_disabled is False


def test_empty_dunning_and_health_kafka_optional_default_false() -> None:
    with patch.dict(
        os.environ,
        {"DUNNING_ENABLED": "", "HEALTH_KAFKA_OPTIONAL": ""},
        clear=True,
    ):
        settings = Settings(_env_file=None)
    assert settings.dunning_enabled is False
    assert settings.health_kafka_optional is False
