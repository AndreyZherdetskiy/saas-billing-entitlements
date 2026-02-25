"""Unit tests for prod-like seed profile helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from seed_prod_like_profiles import (  # noqa: E402
    PROFILES,
    org_idempotency_key,
    subscriptions_count_for_org,
    usage_idempotency_key,
    usage_quantity,
)
from seed_prod_like_status import StatusBucket, status_bucket_for_org  # noqa: E402


def test_tiny_profile_counts() -> None:
    tiny = PROFILES["tiny"]
    assert tiny.organizations == 10
    assert tiny.usage_events_total == 5_000
    assert tiny.plans_published_min == 2


def test_org_idempotency_key_stable() -> None:
    assert org_idempotency_key(0) == "pl_org_0000"
    assert org_idempotency_key(42) == "pl_org_0042"


def test_usage_idempotency_key_format() -> None:
    assert usage_idempotency_key(3, 99) == "pl_usage_0003_00000099"


def test_subscriptions_count_deterministic() -> None:
    medium = PROFILES["medium"]
    counts = {subscriptions_count_for_org(i, medium) for i in range(20)}
    assert counts.issubset({1, 2})


def test_usage_quantity_deterministic() -> None:
    assert usage_quantity(1, 1) == usage_quantity(1, 1)
    assert 1 <= usage_quantity(5, 10) <= 10


@pytest.mark.parametrize(
    ("org_idx", "expected"),
    [
        (0, StatusBucket.trialing),
        (1, StatusBucket.active),
        (2, StatusBucket.past_due),
        (3, StatusBucket.canceled),
        (4, StatusBucket.trialing),
    ],
)
def test_tiny_status_mix(org_idx: int, expected: StatusBucket) -> None:
    assert status_bucket_for_org(org_idx, PROFILES["tiny"]) == expected
