"""SLO metric helpers (no-op when OTEL_SDK_DISABLED=true)."""

from __future__ import annotations

from typing import Final

from opentelemetry import metrics
from opentelemetry.metrics import Counter, Histogram

from billing_platform.config import get_settings

METER_NAME: Final[str] = "billing_platform.slo"

SLO_METRIC_NAMES: Final[tuple[str, ...]] = (
    "entitlement_evaluate_total",
    "entitlement_evaluate_duration_seconds",
    "entitlement_cache_hit_ratio",
    "webhook_processing_duration_seconds",
    "outbox_unpublished_count",
    "outbox_lag_seconds",
    "reconciliation_discrepancy_amount_cents",
    "usage_events_ingested_total",
    "ledger_entries_posted_total",
    "dunning_campaigns_active",
    "http_rate_limited_total",
)

_meter: metrics.Meter | None = None
_counters: dict[str, Counter] = {}
_gauges: dict[str, object] = {}
_histograms: dict[str, Histogram] = {}

# Rolling window for cache-hit ratio gauge (process-local).
_evaluate_total: int = 0
_evaluate_cache_hits: int = 0


def _metrics_enabled() -> bool:
    return not get_settings().otel_sdk_disabled


def get_slo_meter() -> metrics.Meter | None:
    global _meter
    if not _metrics_enabled():
        return None
    if _meter is None:
        _meter = metrics.get_meter(METER_NAME)
    return _meter


def _counter(name: str, description: str) -> Counter | None:
    meter = get_slo_meter()
    if meter is None:
        return None
    if name not in _counters:
        _counters[name] = meter.create_counter(name, description=description)
    return _counters[name]


def _gauge(name: str, description: str, *, unit: str = "") -> object | None:
    meter = get_slo_meter()
    if meter is None:
        return None
    if name not in _gauges:
        _gauges[name] = meter.create_gauge(name, description=description, unit=unit)
    return _gauges[name]


def _histogram(name: str, description: str, *, unit: str = "s") -> Histogram | None:
    meter = get_slo_meter()
    if meter is None:
        return None
    if name not in _histograms:
        _histograms[name] = meter.create_histogram(name, description=description, unit=unit)
    return _histograms[name]


def record_outbox_unpublished_count(value: int) -> None:
    gauge = _gauge("outbox_unpublished_count", "Current unpublished outbox messages")
    if gauge is not None:
        gauge.set(float(value))  # type: ignore[attr-defined]


def record_outbox_lag_seconds(value: float) -> None:
    gauge = _gauge("outbox_lag_seconds", "Oldest unpublished outbox age in seconds", unit="s")
    if gauge is not None:
        gauge.set(float(value))  # type: ignore[attr-defined]


def record_reconciliation_discrepancy_amount_cents(value: int) -> None:
    gauge = _gauge(
        "reconciliation_discrepancy_amount_cents",
        "Sum of absolute reconciliation discrepancy amounts in cents for last run",
        unit="cents",
    )
    if gauge is not None:
        gauge.set(float(value))  # type: ignore[attr-defined]


def increment_usage_events_ingested(count: int = 1) -> None:
    counter = _counter("usage_events_ingested_total", "Usage events accepted by ingest")
    if counter is not None and count:
        counter.add(count)


def increment_ledger_entries_posted(count: int = 1) -> None:
    counter = _counter("ledger_entries_posted_total", "Ledger entries posted")
    if counter is not None and count:
        counter.add(count)


def increment_entitlement_evaluate(*, cache_hit: bool, count: int = 1) -> None:
    global _evaluate_total, _evaluate_cache_hits
    counter = _counter("entitlement_evaluate_total", "Entitlement evaluate invocations")
    if counter is not None and count:
        counter.add(count, {"cache_hit": "true" if cache_hit else "false"})
        _evaluate_total += count
        if cache_hit:
            _evaluate_cache_hits += count
        if _evaluate_total > 0:
            record_entitlement_cache_hit_ratio(_evaluate_cache_hits / _evaluate_total)


def record_entitlement_evaluate_duration_seconds(value: float) -> None:
    hist = _histogram(
        "entitlement_evaluate_duration_seconds",
        "Entitlement evaluate latency",
    )
    if hist is not None:
        hist.record(value)


def record_entitlement_cache_hit_ratio(ratio: float) -> None:
    gauge = _gauge(
        "entitlement_cache_hit_ratio",
        "Entitlement cache hit ratio snapshot (0-1)",
        unit="1",
    )
    if gauge is not None:
        gauge.set(float(ratio))  # type: ignore[attr-defined]


def record_webhook_processing_duration_seconds(value: float) -> None:
    hist = _histogram(
        "webhook_processing_duration_seconds",
        "Webhook processing duration",
    )
    if hist is not None:
        hist.record(value)


def record_dunning_campaigns_active(value: int) -> None:
    gauge = _gauge("dunning_campaigns_active", "Active dunning campaigns")
    if gauge is not None:
        gauge.set(float(value))  # type: ignore[attr-defined]


def increment_http_rate_limited(count: int = 1) -> None:
    counter = _counter("http_rate_limited_total", "HTTP 429 responses")
    if counter is not None and count:
        counter.add(count)
