"""Unit: decide_feature semantics per feature_type (boolean/quota/rate_limit/seat)."""

from __future__ import annotations

import pytest

from billing_platform.services.entitlements import decide_feature


@pytest.mark.parametrize(
    ("is_enabled", "expected_allowed"),
    [
        (True, True),
        (False, False),
    ],
)
def test_boolean_ignores_numeric_limits(is_enabled: bool, expected_allowed: bool) -> None:
    decision = decide_feature(
        feature_type="boolean",
        limit=100,
        used=999,
        enforcement="hard",
        is_enabled=is_enabled,
    )
    assert decision.allowed is expected_allowed
    assert decision.limit is None
    assert decision.used is None
    assert decision.remaining is None
    if not expected_allowed:
        assert decision.reason == "feature_disabled"


def test_quota_exhausted_uses_quota_reason() -> None:
    decision = decide_feature(
        feature_type="quota",
        limit=10,
        used=10,
        enforcement="hard",
    )
    assert decision.allowed is False
    assert decision.reason == "quota_exhausted"


def test_quota_soft_exceeded_allows_with_reason() -> None:
    decision = decide_feature(
        feature_type="quota",
        limit=10,
        used=10,
        enforcement="soft",
    )
    assert decision.allowed is True
    assert decision.reason == "quota_exceeded_soft"


def test_rate_limit_exhausted_uses_rate_limit_reason() -> None:
    decision = decide_feature(
        feature_type="rate_limit",
        limit=100,
        used=100,
        enforcement="hard",
    )
    assert decision.allowed is False
    assert decision.reason == "rate_limit_exhausted"


def test_rate_limit_soft_exceeded_uses_rate_limit_reason() -> None:
    decision = decide_feature(
        feature_type="rate_limit",
        limit=100,
        used=100,
        enforcement="soft",
    )
    assert decision.allowed is True
    assert decision.reason == "rate_limit_exceeded_soft"


def test_seat_uses_subscription_seat_quantity_as_capacity() -> None:
    decision = decide_feature(
        feature_type="seat",
        limit=5,
        used=4,
        seats=10,
        enforcement="hard",
        quantity=2,
    )
    assert decision.allowed is True
    assert decision.limit == 10
    assert decision.remaining == 6


def test_seat_exhausted_uses_seat_reason() -> None:
    decision = decide_feature(
        feature_type="seat",
        limit=5,
        used=10,
        seats=10,
        enforcement="hard",
    )
    assert decision.allowed is False
    assert decision.reason == "seat_exhausted"
    assert decision.limit == 10


def test_seat_falls_back_to_plan_limit_when_seats_missing() -> None:
    decision = decide_feature(
        feature_type="seat",
        limit=8,
        used=7,
        seats=None,
        enforcement="hard",
        quantity=2,
    )
    assert decision.allowed is False
    assert decision.reason == "seat_exhausted"
    assert decision.limit == 8


def test_unknown_feature_type_denies_with_misconfigured_reason() -> None:
    decision = decide_feature(
        feature_type="bogus",
        limit=10,
        used=0,
        enforcement="hard",
    )
    assert decision.allowed is False
    assert decision.reason == "feature_misconfigured"
