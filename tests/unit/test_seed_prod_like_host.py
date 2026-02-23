"""Unit tests for prod-like seed host presentation (D3 fixes)."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from seed_prod_like import SeedResult, _result_to_manifest  # noqa: E402
from seed_prod_like_profiles import PROFILES  # noqa: E402
from seed_prod_like_webhooks import resolve_mock_stripe_base_url  # noqa: E402


def test_resolve_mock_stripe_rewrites_compose_dns() -> None:
    assert resolve_mock_stripe_base_url("http://mock-stripe:8001") == "http://localhost:8001"
    assert resolve_mock_stripe_base_url("http://localhost:8001") == "http://localhost:8001"


def test_manifest_reports_honest_org_counts() -> None:
    profile = PROFILES["tiny"]
    result = SeedResult(
        profile=profile.name,
        organization_count=profile.organizations,
        recon_organization_count=profile.recon_discrepancy_orgs,
        organization_count_total=profile.organizations + profile.recon_discrepancy_orgs,
        usage_events_accepted=0,
        usage_events_duplicates=0,
        recon_invoice_ids=[],
        manifest_path=".local/seed-prod-like-output.json",
        organizations=[],
    )
    manifest = _result_to_manifest(result, seeded_at="2026-02-23T12:00:00Z")
    assert manifest["organization_count"] == 10
    assert manifest["recon_organization_count"] == 2
    assert manifest["organization_count_total"] == 12
