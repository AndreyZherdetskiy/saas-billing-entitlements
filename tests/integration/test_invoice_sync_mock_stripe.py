"""Integration: sync local invoice to mock Stripe registry without mutating amounts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from deploy.docker import mock_stripe_app
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.domain.ids import generate_uuidv7
from billing_platform.domain.models.invoice import Invoice
from billing_platform.domain.models.subscription import Subscription, SubscriptionStatus
from billing_platform.domain.models.usage_aggregate import UsageAggregate
from billing_platform.services.catalog import (
    create_plan,
    create_price,
    create_product,
    publish_plan,
)
from billing_platform.services.invoice_sync import sync_invoice_to_mock_stripe
from billing_platform.services.organizations import create_organization
from billing_platform.services.period_close import close_billing_period


class ASGIMockStripeClient:
    """In-process mock Stripe client for integration tests."""

    def __init__(self, transport: ASGITransport, base_url: str) -> None:
        self._transport = transport
        self._base_url = base_url

    async def create_customer(self, *, organization_public_id: str, email: str) -> str:
        async with AsyncClient(
            transport=self._transport,
            base_url=self._base_url,
            timeout=30.0,
        ) as client:
            response = await client.post(
                "/v1/customers",
                json={
                    "organization_public_id": organization_public_id,
                    "email": email,
                },
            )
            response.raise_for_status()
            return str(response.json()["id"])

    async def create_invoice(
        self,
        *,
        customer_id: str,
        amount_cents: int,
        currency: str,
        idempotency_key: str,
    ) -> str:
        async with AsyncClient(
            transport=self._transport,
            base_url=self._base_url,
            timeout=30.0,
        ) as client:
            response = await client.post(
                "/v1/invoices",
                json={
                    "customer_id": customer_id,
                    "amount_cents": amount_cents,
                    "currency": currency,
                    "idempotency_key": idempotency_key,
                },
            )
            response.raise_for_status()
            return str(response.json()["id"])

    async def list_invoices(self) -> list[dict[str, object]]:
        async with AsyncClient(
            transport=self._transport,
            base_url=self._base_url,
            timeout=30.0,
        ) as client:
            response = await client.get("/v1/invoices")
            response.raise_for_status()
            body = response.json()
            data = body.get("data")
            if not isinstance(data, list):
                return []
            return [item for item in data if isinstance(item, dict)]


def _reset_mock_stripe_registry() -> ASGIMockStripeClient:
    mock_stripe_app._customers.clear()
    mock_stripe_app._subscriptions.clear()
    mock_stripe_app._invoices.clear()
    mock_stripe_app._invoice_idempotency.clear()
    transport = ASGITransport(app=mock_stripe_app.app)
    return ASGIMockStripeClient(transport, "http://mock-stripe.test")


async def _seed_closed_invoice(db_session: AsyncSession) -> tuple[int, int]:
    period_start = datetime(2026, 2, 1, tzinfo=UTC)
    period_end = datetime(2026, 2, 28, 23, 59, 59, tzinfo=UTC)

    org = await create_organization(
        db_session,
        name="Invoice Sync Org",
        external_id=f"ext-sync-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-sync-{uuid.uuid4().hex[:8]}",
    )
    product = await create_product(
        db_session,
        key=f"sync_prod_{uuid.uuid4().hex[:6]}",
        name="Sync Product",
    )
    plan = await create_plan(
        db_session,
        product_id=product.id,
        key=f"sync_plan_{uuid.uuid4().hex[:6]}",
        billing_interval="month",
        trial_days=0,
    )
    await create_price(
        db_session,
        plan_id=plan.id,
        unit_amount_cents=100,
        currency="USD",
        pricing_model="per_unit",
        metered_feature_key="api_calls",
    )
    await publish_plan(db_session, plan.id)

    subscription = Subscription(
        organization_id=org.id,
        plan_id=plan.id,
        status=SubscriptionStatus.active.value,
        current_period_start=period_start,
        current_period_end=period_end,
        external_subscription_id=f"sub_{uuid.uuid4().hex[:12]}",
    )
    db_session.add(subscription)
    await db_session.flush()

    db_session.add(
        UsageAggregate(
            public_id=generate_uuidv7(),
            organization_id=org.id,
            feature_key="api_calls",
            hour_start=datetime(2026, 2, 18, 10, 0, tzinfo=UTC),
            quantity=Decimal(10),
        )
    )
    await db_session.flush()

    result = await close_billing_period(
        db_session,
        organization_id=org.id,
        period_start=period_start,
        period_end=period_end,
        idempotency_key=f"pc-sync-{uuid.uuid4().hex[:8]}",
    )
    await db_session.commit()
    return result.invoice_id, result.total_amount_cents


@pytest.mark.integration
async def test_invoice_sync_sets_external_id_and_registry_amount(
    db_session: AsyncSession,
) -> None:
    stripe_client = _reset_mock_stripe_registry()
    invoice_id, expected_total = await _seed_closed_invoice(db_session)

    invoice_before = await db_session.get(Invoice, invoice_id)
    assert invoice_before is not None
    assert invoice_before.external_invoice_id is None
    assert invoice_before.total_amount_cents == expected_total

    external_id = await sync_invoice_to_mock_stripe(
        db_session,
        invoice_id=invoice_id,
        stripe_client=stripe_client,
    )
    await db_session.commit()

    invoice_after = await db_session.get(Invoice, invoice_id)
    assert invoice_after is not None
    assert invoice_after.external_invoice_id == external_id
    assert invoice_after.synced_at is not None
    assert invoice_after.total_amount_cents == expected_total

    registry = await stripe_client.list_invoices()
    stripe_invoice = next(item for item in registry if item["id"] == external_id)
    assert stripe_invoice["amount_due"] == expected_total
    assert stripe_invoice["currency"] == "usd"


@pytest.mark.integration
async def test_invoice_sync_idempotent_no_amount_mutation(
    db_session: AsyncSession,
) -> None:
    stripe_client = _reset_mock_stripe_registry()
    invoice_id, expected_total = await _seed_closed_invoice(db_session)

    first_external = await sync_invoice_to_mock_stripe(
        db_session,
        invoice_id=invoice_id,
        stripe_client=stripe_client,
    )
    await db_session.commit()

    second_external = await sync_invoice_to_mock_stripe(
        db_session,
        invoice_id=invoice_id,
        stripe_client=stripe_client,
    )
    await db_session.commit()

    assert second_external == first_external
    registry = await stripe_client.list_invoices()
    assert len(registry) == 1

    invoice = await db_session.get(Invoice, invoice_id)
    assert invoice is not None
    assert invoice.total_amount_cents == expected_total
