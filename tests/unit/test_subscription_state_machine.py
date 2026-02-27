"""Unit tests for subscription state machine (TZ Appendix A)."""

from __future__ import annotations

import pytest

from billing_platform.domain.models.subscription import SubscriptionStatus
from billing_platform.domain.state_machines.subscription import IllegalTransition, transition


def test_canceled_to_trialing_illegal() -> None:
    with pytest.raises(IllegalTransition):
        transition(SubscriptionStatus.canceled, SubscriptionStatus.trialing)


def test_trialing_to_active_allowed() -> None:
    result = transition(SubscriptionStatus.trialing, SubscriptionStatus.active)
    assert result == SubscriptionStatus.active


def test_canceled_is_terminal() -> None:
    with pytest.raises(IllegalTransition):
        transition(SubscriptionStatus.canceled, SubscriptionStatus.active)
