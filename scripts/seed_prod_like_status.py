"""Status bucket assignment for prod-like subscription mix."""

from __future__ import annotations

from enum import StrEnum

from seed_prod_like_profiles import ProdLikeProfile


class StatusBucket(StrEnum):
    trialing = "trialing"
    active = "active"
    past_due = "past_due"
    canceled = "canceled"
    unpaid = "unpaid"
    incomplete = "incomplete"
    cancel_at_period_end = "cancel_at_period_end"


def status_bucket_for_org(org_idx: int, profile: ProdLikeProfile) -> StatusBucket:
    """Map org index to target subscription status bucket (design §1)."""
    if profile.name == "tiny":
        return (
            StatusBucket.trialing,
            StatusBucket.active,
            StatusBucket.past_due,
            StatusBucket.canceled,
        )[org_idx % 4]

    bucket = org_idx % 100
    if bucket < 40:
        return StatusBucket.active
    if bucket < 60:
        return StatusBucket.trialing
    if bucket < 75:
        return StatusBucket.past_due
    if bucket < 90:
        return StatusBucket.canceled
    return (StatusBucket.unpaid, StatusBucket.incomplete, StatusBucket.cancel_at_period_end)[
        org_idx % 3
    ]


def plan_key_for_org(org_idx: int, profile: ProdLikeProfile, bucket: StatusBucket) -> str:
    """Pick plan key; incomplete bucket uses no-trial enterprise."""
    if bucket == StatusBucket.incomplete and profile.plans_published_min >= 3:
        return "enterprise"
    if org_idx % 3 == 0:
        return "starter"
    if org_idx % 3 == 1:
        return "pro"
    return "enterprise" if profile.plans_published_min >= 3 else "pro"
