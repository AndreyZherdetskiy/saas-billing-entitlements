"""OpenTelemetry tracing and metrics setup (Console or OTLP exporter)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from billing_platform.config import get_settings
from billing_platform.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = get_logger(__name__)

_HEALTH_EXCLUDED_URLS = "/health/live,/health/ready"


def _resolve_service_name(default: str) -> str:
    settings = get_settings()
    return settings.otel_service_name or default


def _build_resource(service_name: str) -> Resource:
    # Resource carries only service.name — never secrets or tenant identifiers.
    return Resource.create({"service.name": service_name})


def _otlp_signal_endpoint(base_or_full: str, signal_path: str) -> str:
    """Normalize OTEL_EXPORTER_OTLP_ENDPOINT (base URL) to a signal-specific HTTP path.

    The Python OTLP HTTP exporters require the full path (e.g. ``/v1/traces``).
    Env vars often set only the collector base (``http://alloy:4318``).
    """
    endpoint = base_or_full.rstrip("/")
    if endpoint.endswith(signal_path):
        return endpoint
    return f"{endpoint}{signal_path}"


def _configure_metrics(resource: Resource, otlp_endpoint: str) -> None:
    metric_endpoint = _otlp_signal_endpoint(otlp_endpoint, "/v1/metrics")

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=metric_endpoint),
        export_interval_millis=15000,
    )
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)


def configure_telemetry(
    app: FastAPI | None = None,
    *,
    service_name: str = "billing-api",
    span_exporter: SpanExporter | None = None,
) -> TracerProvider | None:
    """Configure TracerProvider (+ metrics when OTLP) and optionally instrument FastAPI."""
    settings = get_settings()
    if settings.otel_sdk_disabled:
        return None

    resolved_name = _resolve_service_name(service_name)
    resource = _build_resource(resolved_name)
    provider = TracerProvider(resource=resource)

    if span_exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    elif settings.otel_exporter_otlp_endpoint:
        traces_endpoint = _otlp_signal_endpoint(settings.otel_exporter_otlp_endpoint, "/v1/traces")
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint)))
        _configure_metrics(resource, settings.otel_exporter_otlp_endpoint)
    else:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))

    trace.set_tracer_provider(provider)

    exporter_name = "otlp" if settings.otel_exporter_otlp_endpoint else "console"
    if app is not None:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=provider,
            excluded_urls=_HEALTH_EXCLUDED_URLS,
        )
    logger.info(
        "telemetry_configured",
        service_name=resolved_name,
        exporter=exporter_name,
        fastapi_instrumented=app is not None,
    )

    return provider
