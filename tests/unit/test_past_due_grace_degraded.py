"""Unit: past_due within grace → degraded mode per policy."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from billing_platform.services.entitlements import decide_access
from billing_platform.services.grace import is_grace_active


def test_past_due_in_grace_degraded() -> None:
    decision = decide_access(
        status="past_due",
        grace_active=True,
        enforcement="degraded",
    )
    assert decision.mode == "degraded"
    assert decision.allowed is True


def test_past_due_grace_expired_denied() -> None:
    decision = decide_access(
        status="past_due",
        grace_active=False,
        enforcement="degraded",
    )
    assert decision.mode == "denied"
    assert decision.allowed is False


def test_active_full_access() -> None:
    decision = decide_access(
        status="active",
        grace_active=False,
        enforcement="hard",
    )
    assert decision.mode == "full"
    assert decision.allowed is True


def test_is_grace_active_within_window() -> None:
    entered = datetime(2026, 2, 16, 12, 0, tzinfo=UTC)
    now = entered + timedelta(days=3)
    assert is_grace_active(
        status="past_due",
        grace_period_days=7,
        past_due_entered_at=entered,
        now=now,
    )


def test_is_grace_active_after_window() -> None:
    entered = datetime(2026, 2, 16, tzinfo=UTC)
    now = datetime(2026, 2, 23, tzinfo=UTC)
    assert not is_grace_active(
        status="past_due",
        grace_period_days=7,
        past_due_entered_at=entered,
        now=now,
    )


def test_is_grace_active_requires_past_due_entered_at() -> None:
    now = datetime(2026, 2, 17, tzinfo=UTC)
    assert not is_grace_active(
        status="past_due",
        grace_period_days=7,
        past_due_entered_at=None,
        now=now,
    )
