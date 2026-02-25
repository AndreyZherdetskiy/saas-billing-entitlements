"""Integration: list dunning campaigns admin API."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.config import get_settings
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.dunning import DunningCampaign, DunningCampaignStatus
from billing_platform.domain.models.organization import Organization
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.organizations import create_organization
from billing_platform.services.webhook_processor import process_webhook
from billing_platform.services.webhooks import persist_webhook


@pytest.fixture
def dunning_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUNNING_ENABLED", "true")
    get_settings.cache_clear()


async def _start_campaign_via_payment_failed(
    db_session: AsyncSession,
) -> tuple[Subscription, Organization, DunningCampaign]:
    org = await create_organization(
        db_session,
        name="Dunning List Org",
        external_id=f"ext-dlc-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-dlc-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"dlc_prod_{uuid.uuid4().hex[:6]}",
        name="Dunning List Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"dlc_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
        grace_period_days=7,
    )
    await publish_plan(db_session, plan.id)

    ext_id = f"sub_{uuid.uuid4().hex[:12]}"
    now = datetime.now(UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.active.value,
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        external_subscription_id=ext_id,
    )
    db_session.add(subscription)
    await db_session.flush()

    payload: dict[str, object] = {
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": f"in_{uuid.uuid4().hex[:12]}",
                "object": "invoice",
                "subscription": ext_id,
                "status": "open",
                "attempt_count": 1,
            }
        },
    }
    webhook = await persist_webhook(
        db_session,
        provider_event_id=str(payload["id"]),
        event_type="invoice.payment_failed",
        payload=payload,
    )
    assert webhook is not None

    await process_webhook(db_session, webhook.id)

    result = await db_session.execute(
        select(DunningCampaign).where(DunningCampaign.subscription_id == subscription.id)
    )
    campaign = result.scalar_one()
    assert campaign.status == DunningCampaignStatus.ACTIVE.value
    return subscription, org, campaign


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_campaigns_after_payment_failed(
    api_client: AsyncClient,
    db_session: AsyncSession,
    dunning_enabled: None,
) -> None:
    subscription, org, campaign = await _start_campaign_via_payment_failed(db_session)
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    response = await api_client.get(
        "/v1/admin/dunning/campaigns",
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) >= 1

    match = next(item for item in body if item["id"] == str(campaign.id))
    assert match["status"] == DunningCampaignStatus.ACTIVE.value
    assert match["organization_public_id"] == str(org.public_id)
    assert match["subscription_public_id"] == str(subscription.public_id)
    assert "organization_id" not in match
    assert "subscription_id" not in match


@pytest.mark.integration
@pytest.mark.asyncio
async def test_list_campaigns_filter_by_organization(
    api_client: AsyncClient,
    db_session: AsyncSession,
    dunning_enabled: None,
) -> None:
    _subscription, org, campaign = await _start_campaign_via_payment_failed(db_session)
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    response = await api_client.get(
        "/v1/admin/dunning/campaigns",
        params={"organization_public_id": str(org.public_id)},
        headers={"Authorization": f"Bearer {admin_raw}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(campaign.id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dunning_operator_lists_own_tenant_only(
    api_client: AsyncClient,
    db_session: AsyncSession,
    dunning_enabled: None,
) -> None:
    subscription, org, campaign = await _start_campaign_via_payment_failed(db_session)
    _, operator_raw = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.DUNNING_OPERATOR.value,
    )
    await db_session.commit()

    response = await api_client.get(
        "/v1/admin/dunning/campaigns",
        headers={"Authorization": f"Bearer {operator_raw}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == str(campaign.id)
    assert body[0]["subscription_public_id"] == str(subscription.public_id)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dunning_operator_cross_tenant_filter_returns_403(
    api_client: AsyncClient,
    db_session: AsyncSession,
    dunning_enabled: None,
) -> None:
    _subscription, org, _campaign = await _start_campaign_via_payment_failed(db_session)
    other_org = await create_organization(
        db_session,
        name="Dunning List Org B",
        external_id=f"ext-dlc-b-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-dlc-b-{uuid.uuid4().hex[:8]}",
    )
    _, operator_raw = await create_api_key(
        db_session,
        organization_id=other_org.id,
        role=ApiKeyRole.DUNNING_OPERATOR.value,
    )
    await db_session.commit()

    response = await api_client.get(
        "/v1/admin/dunning/campaigns",
        params={"organization_public_id": str(org.public_id)},
        headers={"Authorization": f"Bearer {operator_raw}"},
    )
    assert response.status_code == 403
