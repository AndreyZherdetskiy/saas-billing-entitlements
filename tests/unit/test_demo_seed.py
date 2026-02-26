"""Unit tests for deterministic local demo seed."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.bootstrap.demo_seed import (
    DEMO_ORG_PUBLIC_ID,
    DEMO_PLATFORM_ADMIN_RAW_KEY,
    ensure_demo_seed,
)
from billing_platform.services.api_keys import authenticate

pytestmark = pytest.mark.asyncio


async def test_ensure_demo_seed_is_idempotent_and_deterministic(
    db_session: AsyncSession,
) -> None:
    first = await ensure_demo_seed(db_session)
    await db_session.commit()

    assert first.platform_admin_key == DEMO_PLATFORM_ADMIN_RAW_KEY
    assert first.organization_public_id == DEMO_ORG_PUBLIC_ID
    assert first.key_created is True

    auth = await authenticate(db_session, DEMO_PLATFORM_ADMIN_RAW_KEY)
    assert auth.role == "platform_admin"

    second = await ensure_demo_seed(db_session)
    await db_session.commit()
    assert second.key_created is False
    assert second.organization_public_id == first.organization_public_id
    assert second.subscription_public_id == first.subscription_public_id
    assert second.plan_id == first.plan_id
