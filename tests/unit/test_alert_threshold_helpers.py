"""Unit tests for SLO alert threshold helpers."""

from __future__ import annotations

import pytest

from billing_platform.config import get_settings
from billing_platform.observability.alerts import (
    ALERT_DEFINITIONS,
    DUNNING_STUCK_ALERT_SECONDS,
    OUTBOX_LAG_ALERT_SECONDS,
    READY_PROBE_FAIL_ALERT_SECONDS,
    RUNBOOK_DUNNING_STUCK,
    RUNBOOK_RECON_MISMATCH,
    get_alert_definition,
    get_runbook_path,
    should_alert_dunning_stuck,
    should_alert_entitlement_latency,
    should_alert_outbox_lag,
    should_alert_ready_probe_fail,
    should_alert_recon_mismatch,
    should_alert_webhook_fail_rate,
)
from billing_platform.observability.metrics import (
    SLO_METRIC_NAMES,
    increment_usage_events_ingested,
    record_outbox_unpublished_count,
    record_reconciliation_discrepancy_amount_cents,
)


def test_should_alert_recon_when_delta_at_threshold() -> None:
    assert should_alert_recon_mismatch(10000, threshold_cents=10000) is True
    assert should_alert_recon_mismatch(-10000, threshold_cents=10000) is True


def test_should_alert_recon_when_delta_below_threshold() -> None:
    assert should_alert_recon_mismatch(9999, threshold_cents=10000) is False
    assert should_alert_recon_mismatch(-9999, threshold_cents=10000) is False


def test_should_alert_recon_uses_settings_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECONCILIATION_ALERT_AMOUNT_CENTS", "5000")
    get_settings.cache_clear()
    assert should_alert_recon_mismatch(5000) is True
    assert should_alert_recon_mismatch(4999) is False
    get_settings.cache_clear()


def test_outbox_lag_threshold() -> None:
    assert should_alert_outbox_lag(OUTBOX_LAG_ALERT_SECONDS + 1) is True
    assert should_alert_outbox_lag(OUTBOX_LAG_ALERT_SECONDS) is False


def test_webhook_fail_rate_threshold() -> None:
    assert should_alert_webhook_fail_rate(1.1) is True
    assert should_alert_webhook_fail_rate(1.0) is False


def test_entitlement_latency_threshold() -> None:
    assert should_alert_entitlement_latency(101.0) is True
    assert should_alert_entitlement_latency(100.0) is False


def test_ready_probe_fail_threshold() -> None:
    assert should_alert_ready_probe_fail(READY_PROBE_FAIL_ALERT_SECONDS + 1) is True
    assert should_alert_ready_probe_fail(READY_PROBE_FAIL_ALERT_SECONDS) is False


def test_dunning_stuck_threshold() -> None:
    assert should_alert_dunning_stuck(DUNNING_STUCK_ALERT_SECONDS + 1) is True
    assert should_alert_dunning_stuck(DUNNING_STUCK_ALERT_SECONDS) is False


def test_alert_definitions_include_dunning_stuck_runbook() -> None:
    dunning = get_alert_definition("DunningStuck")
    assert dunning is not None
    assert dunning.runbook_path == RUNBOOK_DUNNING_STUCK
    assert get_runbook_path("ReconMismatch") == RUNBOOK_RECON_MISMATCH


def test_all_alerts_have_runbooks() -> None:
    assert len(ALERT_DEFINITIONS) == 6
    for alert in ALERT_DEFINITIONS:
        assert alert.runbook_path.startswith("docs/runbooks/")
        assert alert.runbook_path.endswith(".md")


def test_slo_metric_names_cover_documented_minimum() -> None:
    required = {
        "outbox_unpublished_count",
        "reconciliation_discrepancy_amount_cents",
        "outbox_lag_seconds",
        "dunning_campaigns_active",
        "entitlement_evaluate_duration_seconds",
        "webhook_processing_duration_seconds",
    }
    assert required.issubset(set(SLO_METRIC_NAMES))


def test_metric_stubs_noop_when_otel_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    get_settings.cache_clear()
    record_outbox_unpublished_count(42)
    record_reconciliation_discrepancy_amount_cents(10000)
    increment_usage_events_ingested(3)
    get_settings.cache_clear()
