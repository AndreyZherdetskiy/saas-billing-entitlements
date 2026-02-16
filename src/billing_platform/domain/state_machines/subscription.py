"""Subscription state machine (TZ Appendix A)."""

from __future__ import annotations

from billing_platform.domain.models.subscription import SubscriptionStatus


class IllegalTransition(Exception):
    """Raised when a subscription status transition is not allowed."""


ALLOWED_TRANSITIONS: dict[SubscriptionStatus, set[SubscriptionStatus]] = {
    SubscriptionStatus.incomplete: {
        SubscriptionStatus.active,
        SubscriptionStatus.canceled,
    },
    SubscriptionStatus.trialing: {
        SubscriptionStatus.active,
        SubscriptionStatus.canceled,
    },
    SubscriptionStatus.active: {
        SubscriptionStatus.canceled,
        SubscriptionStatus.past_due,
    },
    SubscriptionStatus.past_due: {
        SubscriptionStatus.active,
        SubscriptionStatus.unpaid,
        SubscriptionStatus.canceled,
    },
    SubscriptionStatus.canceled: set(),
    SubscriptionStatus.unpaid: set(),
}


def transition(
    current: SubscriptionStatus,
    new: SubscriptionStatus,
) -> SubscriptionStatus:
    """Return *new* when the transition is legal; otherwise raise IllegalTransition."""
    if new not in ALLOWED_TRANSITIONS[current]:
        raise IllegalTransition(f"illegal transition: {current.value} -> {new.value}")
    return new
