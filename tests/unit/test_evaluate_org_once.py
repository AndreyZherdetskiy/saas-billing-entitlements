"""Unit: evaluate uses the passed Organization; no second public_id fetch."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.entitlements import Check, evaluate
from billing_platform.services.organizations import create_organization


@pytest.mark.asyncio
async def test_evaluate_does_not_call_get_organization_by_public_id_when_org_passed(
    db_session: AsyncSession,
) -> None:
    org = await create_organization(
        db_session,
        name="Eval Once Org",
        external_id=f"ext-eval-once-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-eval-once-{uuid.uuid4().hex[:8]}",
    )
    await db_session.commit()

    async def fake_get_or_build(
        *_args: object,
        **_kwargs: object,
    ) -> tuple[dict[str, Any], bool]:
        return (
            {
                "subscription_status": "active",
                "grace_active": False,
                "features": {},
                "cache_version": 7,
            },
            True,
        )

    with (
        patch(
            "billing_platform.services.entitlements.get_or_build_cached_snapshot",
            new=AsyncMock(side_effect=fake_get_or_build),
        ),
        patch(
            "billing_platform.services.organizations.get_organization_by_public_id",
            new_callable=AsyncMock,
        ) as org_spy,
        patch(
            "billing_platform.services.entitlements.get_organization_by_public_id",
            new_callable=AsyncMock,
            create=True,
        ) as local_spy,
    ):
        response = await evaluate(
            MagicMock(),
            organization_id=org.id,
            organization_public_id=org.public_id,
            checks=[Check(feature_key="api_calls", quantity=1)],
            session=db_session,
        )

    org_spy.assert_not_called()
    local_spy.assert_not_called()
    assert response.organization_public_id == str(org.public_id)
    assert response.version == 7
    assert response.cache_hit is True
    assert response.subscription_status == "active"


@pytest.mark.asyncio
async def test_authenticate_sets_organization_public_id(
    db_session: AsyncSession,
) -> None:
    org = await create_organization(
        db_session,
        name="Auth Join Org",
        external_id=f"ext-auth-join-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-auth-join-{uuid.uuid4().hex[:8]}",
    )
    _key, raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    from billing_platform.services.api_keys import authenticate

    ctx = await authenticate(db_session, raw)
    assert ctx.organization_id == org.id
    assert ctx.organization_public_id == org.public_id


@pytest.mark.asyncio
async def test_authenticate_platform_admin_organization_public_id_is_none(
    db_session: AsyncSession,
) -> None:
    _key, raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    from billing_platform.services.api_keys import authenticate

    ctx = await authenticate(db_session, raw)
    assert ctx.organization_id is None
    assert ctx.organization_public_id is None
