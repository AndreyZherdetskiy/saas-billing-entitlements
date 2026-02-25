"""Integration: dunning pause/resume admin API and RBAC."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.config import get_settings
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.domain.models.dunning import DunningAttempt, DunningCampaignStatus
from billing_platform.domain.models.invoice import Invoice, InvoiceStatus
from billing_platform.domain.models.outbox_message import OutboxMessage
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.catalog import create_plan, create_product, publish_plan
from billing_platform.services.dunning import process_due_attempts, start_campaign
from billing_platform.services.organizations import create_organization


@pytest.fixture
def dunning_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUNNING_ENABLED", "true")
    get_settings.cache_clear()


async def _seed_campaign(
    db_session: AsyncSession,
) -> tuple[object, object]:
    org = await create_organization(
        db_session,
        name="Dunning Pause Org",
        external_id=f"ext-dpr-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-dpr-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"dpr_prod_{uuid.uuid4().hex[:6]}",
        name="Dunning Pause Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"dpr_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
        grace_period_days=7,
    )
    await publish_plan(db_session, plan.id)

    started_at = datetime(2026, 2, 16, 12, 0, tzinfo=UTC)
    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.past_due.value,
        current_period_start=started_at,
        current_period_end=started_at + timedelta(days=30),
        external_subscription_id=f"sub_{uuid.uuid4().hex[:12]}",
        past_due_entered_at=started_at,
    )
    db_session.add(subscription)
    await db_session.flush()

    campaign = await start_campaign(
        db_session,
        subscription_id=subscription.id,
        organization_id=org.id,
        idempotency_key=f"dunning:api:{uuid.uuid4().hex[:8]}",
        started_at=started_at,
    )
    assert campaign is not None
    return campaign, subscription


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pause_and_resume_via_admin_api(
    api_client: AsyncClient,
    db_session: AsyncSession,
    dunning_enabled: None,
) -> None:
    campaign, _subscription = await _seed_campaign(db_session)
    _, admin_raw = await create_api_key(
        db_session,
        organization_id=None,
        role=ApiKeyRole.PLATFORM_ADMIN.value,
    )
    await db_session.commit()

    headers = {"Authorization": f"Bearer {admin_raw}"}
    pause_resp = await api_client.post(
        f"/v1/admin/dunning/campaigns/{campaign.id}/pause",
        headers=headers,
    )
    assert pause_resp.status_code == 200
    pause_body = pause_resp.json()
    assert pause_body["status"] == DunningCampaignStatus.PAUSED.value
    assert "subscription_id" not in pause_body
    assert "organization_id" not in pause_body
    assert "subscription_public_id" in pause_body
    assert "organization_public_id" in pause_body

    resume_resp = await api_client.post(
        f"/v1/admin/dunning/campaigns/{campaign.id}/resume",
        headers=headers,
    )
    assert resume_resp.status_code == 200
    assert resume_resp.json()["status"] == DunningCampaignStatus.ACTIVE.value


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dunning_operator_may_pause_resume(
    api_client: AsyncClient,
    db_session: AsyncSession,
    dunning_enabled: None,
) -> None:
    campaign, subscription = await _seed_campaign(db_session)
    _, operator_raw = await create_api_key(
        db_session,
        organization_id=subscription.organization_id,
        role=ApiKeyRole.DUNNING_OPERATOR.value,
    )
    await db_session.commit()

    headers = {"Authorization": f"Bearer {operator_raw}"}
    pause_resp = await api_client.post(
        f"/v1/admin/dunning/campaigns/{campaign.id}/pause",
        headers=headers,
    )
    assert pause_resp.status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio
async def test_dunning_operator_cross_tenant_pause_returns_403(
    api_client: AsyncClient,
    db_session: AsyncSession,
    dunning_enabled: None,
) -> None:
    campaign, _subscription = await _seed_campaign(db_session)
    org_b = await create_organization(
        db_session,
        name="Dunning Org B",
        external_id=f"ext-dpr-b-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-org-dpr-b-{uuid.uuid4().hex[:8]}",
    )
    _, operator_raw = await create_api_key(
        db_session,
        organization_id=org_b.id,
        role=ApiKeyRole.DUNNING_OPERATOR.value,
    )
    await db_session.commit()

    response = await api_client.post(
        f"/v1/admin/dunning/campaigns/{campaign.id}/pause",
        headers={"Authorization": f"Bearer {operator_raw}"},
    )
    assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_product_service_cannot_pause_campaign(
    api_client: AsyncClient,
    db_session: AsyncSession,
    dunning_enabled: None,
) -> None:
    campaign, subscription = await _seed_campaign(db_session)
    _, tenant_raw = await create_api_key(
        db_session,
        organization_id=subscription.organization_id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    response = await api_client.post(
        f"/v1/admin/dunning/campaigns/{campaign.id}/pause",
        headers={"Authorization": f"Bearer {tenant_raw}"},
    )
    assert response.status_code == 403


@pytest.mark.integration
@pytest.mark.asyncio
async def test_paused_campaign_process_due_has_no_side_effects(
    db_session: AsyncSession,
    dunning_enabled: None,
) -> None:
    campaign, subscription = await _seed_campaign(db_session)
    started_at = datetime(2026, 2, 16, 12, 0, tzinfo=UTC)

    invoice = Invoice(
        organization_id=subscription.organization_id,
        subscription_id=subscription.id,
        status=InvoiceStatus.open.value,
        currency="USD",
        period_start=started_at,
        period_end=started_at + timedelta(days=30),
        total_amount_cents=2000,
        external_invoice_id=f"in_{uuid.uuid4().hex[:12]}",
        idempotency_key=f"inv:dpr:{uuid.uuid4().hex[:8]}",
    )
    db_session.add(invoice)

    _, operator_key, operator_raw = await _create_operator_with_id(
        db_session, subscription.organization_id
    )
    await pause_campaign_direct(
        db_session,
        campaign.id,
        operator_key.id,
        organization_id=subscription.organization_id,
    )
    await db_session.commit()

    outbox_before = await db_session.scalar(select(func.count()).select_from(OutboxMessage))
    now = started_at + timedelta(days=8)

    with patch(
        "billing_platform.services.dunning.MockStripeClient.retry_invoice_payment",
        new=AsyncMock(return_value={"status": "open"}),
    ) as mock_retry:
        processed = await process_due_attempts(db_session, now=now)

    assert processed == 0
    mock_retry.assert_not_called()
    outbox_after = await db_session.scalar(select(func.count()).select_from(OutboxMessage))
    assert outbox_after == outbox_before

    attempt_count = await db_session.scalar(
        select(func.count())
        .select_from(DunningAttempt)
        .where(DunningAttempt.campaign_id == campaign.id, DunningAttempt.executed_at.is_(None))
    )
    assert attempt_count == 3


async def _create_operator_with_id(
    db_session: AsyncSession,
    organization_id: int,
) -> tuple[object, object, str]:
    api_key, raw = await create_api_key(
        db_session,
        organization_id=organization_id,
        role=ApiKeyRole.DUNNING_OPERATOR.value,
    )
    return api_key, api_key, raw


async def pause_campaign_direct(
    db_session: AsyncSession,
    campaign_id: uuid.UUID,
    actor_key_id: uuid.UUID,
    *,
    organization_id: int,
) -> None:
    from billing_platform.services.dunning import pause_campaign

    await pause_campaign(
        db_session,
        campaign_public_id=campaign_id,
        organization_id=organization_id,
        actor_key_id=actor_key_id,
    )
