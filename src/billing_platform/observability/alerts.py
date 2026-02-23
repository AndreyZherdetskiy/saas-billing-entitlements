"""SLO alert threshold helpers and runbook wiring (docs/slo.md)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from billing_platform.config import get_settings

# Default thresholds aligned with docs/slo.md (overridable per-call or via Settings).
OUTBOX_LAG_ALERT_SECONDS: Final[int] = 300
WEBHOOK_FAIL_RATE_ALERT_PCT: Final[float] = 1.0
ENTITLEMENT_LATENCY_ALERT_MS: Final[float] = 100.0
READY_PROBE_FAIL_ALERT_SECONDS: Final[int] = 120
DUNNING_STUCK_ALERT_SECONDS: Final[int] = 3600

RUNBOOK_OUTBOX_LAG: Final[str] = "docs/runbooks/outbox-lag.md"
RUNBOOK_WEBHOOK_REPLAY: Final[str] = "docs/runbooks/webhook-replay.md"
RUNBOOK_RECON_MISMATCH: Final[str] = "docs/runbooks/reconciliation-mismatch.md"
RUNBOOK_ENTITLEMENT_LATENCY: Final[str] = "docs/runbooks/entitlement-latency.md"
RUNBOOK_READY_PROBE_FAIL: Final[str] = "docs/runbooks/ready-probe-fail.md"
RUNBOOK_DUNNING_STUCK: Final[str] = "docs/runbooks/dunning-stuck.md"


@dataclass(frozen=True, slots=True)
class AlertDefinition:
    name: str
    condition: str
    priority: str
    runbook_path: str
    metric_names: tuple[str, ...] = ()


ALERT_DEFINITIONS: Final[tuple[AlertDefinition, ...]] = (
    AlertDefinition(
        name="OutboxLagHigh",
        condition=f"outbox_lag_seconds > {OUTBOX_LAG_ALERT_SECONDS}",
        priority="P2",
        runbook_path=RUNBOOK_OUTBOX_LAG,
        metric_names=("outbox_lag_seconds", "outbox_unpublished_count"),
    ),
    AlertDefinition(
        name="WebhookFailRate",
        condition=f"failed_rate > {WEBHOOK_FAIL_RATE_ALERT_PCT}% за 15 мин",
        priority="P2",
        runbook_path=RUNBOOK_WEBHOOK_REPLAY,
        metric_names=("webhook_processing_duration_seconds",),
    ),
    AlertDefinition(
        name="ReconMismatch",
        condition="discrepancy amount > $100 (default 10000 cents)",
        priority="P3",
        runbook_path=RUNBOOK_RECON_MISMATCH,
        metric_names=("reconciliation_discrepancy_amount_cents",),
    ),
    AlertDefinition(
        name="EntitlementLatency",
        condition=f"p99 > {ENTITLEMENT_LATENCY_ALERT_MS} мс 5 мин",
        priority="P3",
        runbook_path=RUNBOOK_ENTITLEMENT_LATENCY,
        metric_names=("entitlement_evaluate_duration_seconds", "entitlement_evaluate_total"),
    ),
    AlertDefinition(
        name="ReadyProbeFail",
        condition=f"ready fails > {READY_PROBE_FAIL_ALERT_SECONDS // 60} мин",
        priority="P1",
        runbook_path=RUNBOOK_READY_PROBE_FAIL,
        metric_names=(),
    ),
    AlertDefinition(
        name="DunningStuck",
        condition=f"attempt overdue > {DUNNING_STUCK_ALERT_SECONDS // 3600} ч (этап 2)",
        priority="P3",
        runbook_path=RUNBOOK_DUNNING_STUCK,
        metric_names=("dunning_campaigns_active",),
    ),
)

_ALERT_BY_NAME: Final[dict[str, AlertDefinition]] = {a.name: a for a in ALERT_DEFINITIONS}


def get_alert_definition(name: str) -> AlertDefinition | None:
    return _ALERT_BY_NAME.get(name)


def get_runbook_path(alert_name: str) -> str | None:
    definition = get_alert_definition(alert_name)
    return definition.runbook_path if definition is not None else None


def should_alert_recon_mismatch(
    delta_cents: int,
    *,
    threshold_cents: int | None = None,
) -> bool:
    """ReconMismatch when abs(delta) >= threshold (Settings default: $100)."""
    if threshold_cents is not None:
        limit = threshold_cents
    else:
        limit = get_settings().reconciliation_alert_amount_cents
    return abs(delta_cents) >= limit


def should_alert_outbox_lag(
    lag_seconds: float,
    *,
    threshold_seconds: int | None = None,
) -> bool:
    limit = threshold_seconds if threshold_seconds is not None else OUTBOX_LAG_ALERT_SECONDS
    return lag_seconds > limit


def should_alert_webhook_fail_rate(
    failed_rate_pct: float,
    *,
    threshold_pct: float | None = None,
) -> bool:
    limit = threshold_pct if threshold_pct is not None else WEBHOOK_FAIL_RATE_ALERT_PCT
    return failed_rate_pct > limit


def should_alert_entitlement_latency(
    p99_ms: float,
    *,
    threshold_ms: float | None = None,
) -> bool:
    limit = threshold_ms if threshold_ms is not None else ENTITLEMENT_LATENCY_ALERT_MS
    return p99_ms > limit


def should_alert_ready_probe_fail(
    fail_duration_seconds: float,
    *,
    threshold_seconds: int | None = None,
) -> bool:
    limit = threshold_seconds if threshold_seconds is not None else READY_PROBE_FAIL_ALERT_SECONDS
    return fail_duration_seconds > limit


def should_alert_dunning_stuck(
    overdue_seconds: float,
    *,
    threshold_seconds: int | None = None,
) -> bool:
    limit = threshold_seconds if threshold_seconds is not None else DUNNING_STUCK_ALERT_SECONDS
    return overdue_seconds > limit
