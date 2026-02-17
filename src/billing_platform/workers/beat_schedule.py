"""Central Celery Beat schedule for billing background jobs.

ADR-004: entries only enqueue Celery tasks; domain facts reach Kafka via outbox
in the same DB transaction inside task handlers — never direct publish from Beat.
"""

from __future__ import annotations

from celery.schedules import crontab

from billing_platform.config import get_settings


def build_beat_schedule() -> dict[str, dict[str, object]]:
    """Return the unified periodic-task schedule for billing workers."""
    settings = get_settings()
    enforcement_interval = max(1, settings.grace_enforcement_interval_seconds)

    return {
        "usage-aggregate-hourly": {
            "task": "usage.aggregate_hourly_sweep",
            "schedule": crontab(minute=5),
        },
        "subscription-enforce-grace-expiry": {
            "task": "subscription.enforce_grace_expiry",
            "schedule": enforcement_interval,
        },
        "dunning-process-due-attempts": {
            "task": "dunning.process_due_attempts",
            "schedule": enforcement_interval,
        },
        "reconciliation-daily": {
            "task": "reconciliation.daily",
            "schedule": crontab(hour=2, minute=0),
        },
        "usage-create-partition-daily": {
            "task": "usage.create_partition",
            "schedule": crontab(hour=1, minute=15),
        },
        "usage-create-partition-monthly": {
            "task": "usage.create_partition",
            "schedule": crontab(day_of_month=1, hour=0, minute=10),
        },
    }
