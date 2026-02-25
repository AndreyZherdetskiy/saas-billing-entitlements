"""Profile definitions for prod-like seed (design SoT: prod-like-dataset-design.md)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProdLikeProfile:
    name: str
    organizations: int
    plans_published_min: int
    include_archived_draft: bool
    subscriptions_per_org: tuple[int, int]
    api_keys_per_org: dict[str, int]
    platform_admin_keys: int
    usage_events_total: int
    invoice_paid_org_fraction: float
    recon_discrepancy_orgs: int
    usage_batch_size: int
    dunning_campaigns: bool


PROFILES: dict[str, ProdLikeProfile] = {
    "tiny": ProdLikeProfile(
        name="tiny",
        organizations=10,
        plans_published_min=2,
        include_archived_draft=False,
        subscriptions_per_org=(1, 1),
        api_keys_per_org={"product_service": 1, "revops_read": 1},
        platform_admin_keys=1,
        usage_events_total=5_000,
        invoice_paid_org_fraction=0.20,
        recon_discrepancy_orgs=2,
        usage_batch_size=200,
        dunning_campaigns=False,
    ),
    "medium": ProdLikeProfile(
        name="medium",
        organizations=50,
        plans_published_min=3,
        include_archived_draft=False,
        subscriptions_per_org=(1, 2),
        api_keys_per_org={
            "product_service": 1,
            "revops_read": 1,
            "support_read": 1,
        },
        platform_admin_keys=1,
        usage_events_total=50_000,
        invoice_paid_org_fraction=0.40,
        recon_discrepancy_orgs=5,
        usage_batch_size=500,
        dunning_campaigns=True,
    ),
    "full": ProdLikeProfile(
        name="full",
        organizations=200,
        plans_published_min=3,
        include_archived_draft=True,
        subscriptions_per_org=(1, 3),
        api_keys_per_org={
            "product_service": 1,
            "revops_read": 1,
            "support_read": 1,
        },
        platform_admin_keys=2,
        usage_batch_size=1_000,
        usage_events_total=500_000,
        invoice_paid_org_fraction=0.40,
        recon_discrepancy_orgs=10,
        dunning_campaigns=True,
    ),
}


def org_idempotency_key(org_idx: int) -> str:
    return f"pl_org_{org_idx:04d}"


def sub_idempotency_key(org_idx: int, sub_slot: int) -> str:
    return f"sub_pl_{org_idx:04d}_{sub_slot}"


def external_subscription_id(org_idx: int, sub_slot: int) -> str:
    return f"sub_pl_{org_idx:04d}_{sub_slot}"


def usage_idempotency_key(org_idx: int, seq: int) -> str:
    return f"pl_usage_{org_idx:04d}_{seq:08d}"


def subscriptions_count_for_org(org_idx: int, profile: ProdLikeProfile) -> int:
    lo, hi = profile.subscriptions_per_org
    if lo == hi:
        return lo
    return lo + (org_idx % (hi - lo + 1))


def invoice_paid_org_indices(profile: ProdLikeProfile) -> range:
    count = int(profile.organizations * profile.invoice_paid_org_fraction)
    return range(count)


def usage_quantity(org_idx: int, seq: int) -> int:
    return 1 + ((org_idx * 17 + seq * 13) % 10)
