"""Unit: compute_grace_until policy."""

from __future__ import annotations

from datetime import UTC, datetime

from billing_platform.services.grace import compute_grace_until


def test_grace_until_plus_seven_days() -> None:
    entered = datetime(2026, 2, 16, tzinfo=UTC)
    assert compute_grace_until(past_due_entered_at=entered, grace_period_days=7) == datetime(
        2026, 2, 23, tzinfo=UTC
    )
