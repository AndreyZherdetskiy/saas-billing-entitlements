"""Instrument kinds for SLO helpers (gauges/histograms, not additive snapshots)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from billing_platform.config import get_settings
from billing_platform.observability import metrics as slo_metrics


def _install_test_meter() -> tuple[InMemoryMetricReader, object]:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    meter = provider.get_meter(slo_metrics.METER_NAME)
    slo_metrics._counters.clear()
    slo_metrics._gauges.clear()
    slo_metrics._histograms.clear()
    return reader, meter


def _points_by_name(reader: InMemoryMetricReader) -> dict[str, object]:
    data = reader.get_metrics_data()
    assert data is not None
    found: dict[str, object] = {}
    for resource_metric in data.resource_metrics:
        for scope_metric in resource_metric.scope_metrics:
            for metric in scope_metric.metrics:
                found[metric.name] = metric.data
    return found


def test_outbox_lag_gauge_sets_absolute_value_not_sum(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    get_settings.cache_clear()
    reader, meter = _install_test_meter()

    with patch.object(slo_metrics, "get_slo_meter", return_value=meter):
        slo_metrics.record_outbox_lag_seconds(10.0)
        slo_metrics.record_outbox_lag_seconds(3.0)

    points = _points_by_name(reader)
    assert "outbox_lag_seconds" in points
    data = points["outbox_lag_seconds"]
    assert type(data).__name__ == "Gauge"
    assert data.data_points[0].value == 3.0


def test_webhook_duration_is_histogram(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    get_settings.cache_clear()
    reader, meter = _install_test_meter()

    with patch.object(slo_metrics, "get_slo_meter", return_value=meter):
        slo_metrics.record_webhook_processing_duration_seconds(0.25)
        slo_metrics.record_webhook_processing_duration_seconds(0.5)

    points = _points_by_name(reader)
    assert "webhook_processing_duration_seconds" in points
    data = points["webhook_processing_duration_seconds"]
    assert type(data).__name__ == "Histogram"
    assert data.data_points[0].count == 2
    assert data.data_points[0].sum == 0.75


def test_entitlement_evaluate_counter_and_duration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    get_settings.cache_clear()
    reader, meter = _install_test_meter()

    with patch.object(slo_metrics, "get_slo_meter", return_value=meter):
        slo_metrics.increment_entitlement_evaluate(cache_hit=True)
        slo_metrics.record_entitlement_evaluate_duration_seconds(0.012)
        slo_metrics.record_entitlement_cache_hit_ratio(0.8)

    points = _points_by_name(reader)
    assert "entitlement_evaluate_total" in points
    assert "entitlement_evaluate_duration_seconds" in points
    assert "entitlement_cache_hit_ratio" in points
    assert points["entitlement_cache_hit_ratio"].data_points[0].value == 0.8
