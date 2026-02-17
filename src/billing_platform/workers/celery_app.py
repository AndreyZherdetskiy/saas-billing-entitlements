"""Celery application for billing background workers."""

from __future__ import annotations

import sys

from celery import Celery

from billing_platform.config import get_settings
from billing_platform.telemetry import configure_telemetry
from billing_platform.workers.beat_schedule import build_beat_schedule

settings = get_settings()

# Compose sets OTEL_SERVICE_NAME per container; argv distinguishes worker vs beat locally.
_otel_service = "billing-beat" if "beat" in sys.argv else "billing-worker"
configure_telemetry(service_name=_otel_service)

celery_app = Celery(
    "billing_platform",
    broker=settings.redis_url,
    backend=settings.redis_url,
)
celery_app.conf.task_default_queue = "billing"
celery_app.conf.task_acks_late = True
celery_app.conf.worker_prefetch_multiplier = 1
celery_app.conf.beat_schedule = build_beat_schedule()

import billing_platform.workers.tasks.aggregate_usage_hourly  # noqa: E402, F401
import billing_platform.workers.tasks.dunning_steps  # noqa: E402, F401
import billing_platform.workers.tasks.enforce_grace_expiry  # noqa: E402, F401
import billing_platform.workers.tasks.invoice_sync  # noqa: E402, F401
import billing_platform.workers.tasks.period_close  # noqa: E402, F401
import billing_platform.workers.tasks.reconciliation_daily  # noqa: E402, F401
import billing_platform.workers.tasks.usage_partition_celery  # noqa: E402, F401
