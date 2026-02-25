"""Integration: prod-like seed idempotency (Task D2 RED/GREEN)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import get_settings

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from seed_prod_like import _result_to_manifest, run_prod_like_seed  # noqa: E402
from seed_prod_like_profiles import PROFILES  # noqa: E402
from seed_prod_like_queries import count_pl_main_orgs  # noqa: E402

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def seed_session_factory(migrated_postgres_url: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DUNNING_ENABLED", "false")
    get_settings.cache_clear()
    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    await engine.dispose()
    get_settings.cache_clear()


async def test_reseed_tiny_does_not_increase_pl_org_count(seed_session_factory) -> None:
    """Re-running tiny profile must not grow pl_org_* organization count."""
    profile = PROFILES["tiny"]
    settings = get_settings()

    async with seed_session_factory() as session:
        await run_prod_like_seed(session, settings, profile)
        await session.commit()
        count_after_first = await count_pl_main_orgs(session)

    async with seed_session_factory() as session:
        second = await run_prod_like_seed(session, settings, profile)
        await session.commit()
        count_after_second = await count_pl_main_orgs(session)

    assert count_after_first == profile.organizations
    assert count_after_second == count_after_first
    assert second.usage_events_duplicates == profile.usage_events_total
    assert second.usage_events_accepted == 0


async def test_manifest_has_no_bigint_ids(seed_session_factory) -> None:
    """Manifest payload must expose public_id strings only (no organization_id BIGINT)."""
    profile = PROFILES["tiny"]
    settings = get_settings()

    async with seed_session_factory() as session:
        result = await run_prod_like_seed(session, settings, profile)
        await session.commit()

    manifest = _result_to_manifest(
        result,
        seeded_at="2026-02-23T12:00:00Z",
    )
    payload = json.dumps(manifest)
    assert "organization_id" not in payload
    assert "subscription_id" not in payload
    assert result.organizations[0].public_id
    assert manifest["organization_count"] == profile.organizations
    assert manifest["recon_organization_count"] == profile.recon_discrepancy_orgs
    assert manifest["organization_count_total"] == (
        profile.organizations + profile.recon_discrepancy_orgs
    )
