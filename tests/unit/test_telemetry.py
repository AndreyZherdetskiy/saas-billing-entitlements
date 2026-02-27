"""Unit tests for OpenTelemetry telemetry configuration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from billing_platform.config import get_settings
from billing_platform.telemetry import configure_telemetry


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> None:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_telemetry_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    get_settings.cache_clear()

    provider = configure_telemetry(service_name="billing-api")

    assert provider is None


def test_telemetry_otlp_endpoint_configures_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://alloy:4318")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "billing-api")
    get_settings.cache_clear()

    with (
        patch("billing_platform.telemetry.OTLPSpanExporter") as mock_span_exporter,
        patch("billing_platform.telemetry.OTLPMetricExporter") as mock_metric_exporter,
        patch("billing_platform.telemetry.PeriodicExportingMetricReader") as mock_reader,
        patch("billing_platform.telemetry.MeterProvider") as mock_meter_provider,
        patch("billing_platform.telemetry.metrics.set_meter_provider") as mock_set_meter,
    ):
        mock_span_exporter.return_value = MagicMock()
        mock_metric_exporter.return_value = MagicMock()
        mock_reader.return_value = MagicMock()
        provider = configure_telemetry(service_name="billing-api")

    assert provider is not None
    mock_span_exporter.assert_called_once_with(endpoint="http://alloy:4318/v1/traces")
    mock_metric_exporter.assert_called_once_with(endpoint="http://alloy:4318/v1/metrics")
    mock_meter_provider.assert_called_once()
    mock_set_meter.assert_called_once()
    resource_attrs = dict(provider.resource.attributes)
    assert resource_attrs["service.name"] == "billing-api"
    assert "authorization" not in resource_attrs
    assert "api_key" not in resource_attrs


def test_telemetry_resource_has_no_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    monkeypatch.setenv("OTEL_SERVICE_NAME", "billing-api")
    get_settings.cache_clear()

    exporter = InMemorySpanExporter()
    provider = configure_telemetry(service_name="billing-api", span_exporter=exporter)

    assert provider is not None
    resource_attrs = dict(provider.resource.attributes)
    for key, value in resource_attrs.items():
        key_lower = key.lower()
        assert "authorization" not in key_lower
        assert "api_key" not in key_lower
        if isinstance(value, str):
            assert not value.startswith("Bearer ")
            assert not value.startswith("sk_")


def test_telemetry_service_name_defaults_to_entrypoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    get_settings.cache_clear()

    exporter = InMemorySpanExporter()
    provider = configure_telemetry(service_name="outbox-relay", span_exporter=exporter)

    assert provider is not None
    assert dict(provider.resource.attributes)["service.name"] == "outbox-relay"
