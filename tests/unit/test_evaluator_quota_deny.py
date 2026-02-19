"""Unit: quota exhausted → deny (hard enforcement)."""

from __future__ import annotations

from billing_platform.services.entitlements import decide_feature


def test_quota_exhausted_deny() -> None:
    decision = decide_feature(
        feature_type="quota",
        limit=10,
        used=10,
        enforcement="hard",
    )
    assert decision.allowed is False
    assert decision.reason == "quota_exhausted"
    assert decision.remaining == 0


def test_quota_within_limit_allow() -> None:
    decision = decide_feature(
        feature_type="quota",
        limit=10,
        used=5,
        enforcement="hard",
        quantity=1,
    )
    assert decision.allowed is True
    assert decision.remaining == 5
