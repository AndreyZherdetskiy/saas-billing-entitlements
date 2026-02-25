"""Mock Stripe + webhook helpers for prod-like seed."""

from __future__ import annotations

import sys
from urllib.parse import urlparse, urlunparse

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from billing_platform.config import Settings
from billing_platform.domain.models.subscription import Subscription
from billing_platform.services.webhook_processor import process_webhook
from billing_platform.services.webhooks import persist_webhook


def resolve_mock_stripe_base_url(base_url: str) -> str:
    """Rewrite compose-only ``mock-stripe`` hostname for host-side script runs."""
    stripped = base_url.rstrip("/")
    parsed = urlparse(stripped)
    if parsed.hostname != "mock-stripe":
        return stripped
    port = parsed.port
    netloc = f"localhost:{port}" if port else "localhost"
    return urlunparse(parsed._replace(netloc=netloc))


async def ensure_mock_stripe_invoice(
    settings: Settings,
    *,
    invoice_id: str,
    amount_cents: int,
    external_subscription_id: str,
) -> None:
    """Register invoice in mock Stripe registry (best-effort)."""
    base_url = resolve_mock_stripe_base_url(settings.mock_stripe_base_url)
    invoice_payload = {
        "id": invoice_id,
        "object": "invoice",
        "status": "paid",
        "amount_due": amount_cents,
        "amount_paid": amount_cents,
        "currency": "usd",
        "subscription": external_subscription_id,
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        try:
            health = await client.get("/health")
            health.raise_for_status()
        except (httpx.HTTPError, OSError):
            print(
                "warning: mock-stripe unreachable; continuing without registry seed",
                file=sys.stderr,
            )
            return
        response = await client.post("/v1/test/seed-invoice", json=invoice_payload)
        if response.status_code == 404:
            print(
                "warning: mock-stripe /v1/test/seed-invoice not available",
                file=sys.stderr,
            )
        else:
            response.raise_for_status()


def _invoice_paid_payload(
    *,
    provider_event_id: str,
    invoice_id: str,
    external_subscription_id: str,
    amount_paid: int,
) -> dict[str, object]:
    return {
        "id": provider_event_id,
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": invoice_id,
                "object": "invoice",
                "subscription": external_subscription_id,
                "status": "paid",
                "amount_paid": amount_paid,
                "currency": "usd",
            }
        },
    }


def _payment_failed_payload(
    *,
    provider_event_id: str,
    invoice_id: str,
    external_subscription_id: str,
) -> dict[str, object]:
    return {
        "id": provider_event_id,
        "type": "invoice.payment_failed",
        "data": {
            "object": {
                "id": invoice_id,
                "object": "invoice",
                "subscription": external_subscription_id,
                "status": "open",
                "attempt_count": 1,
            }
        },
    }


async def apply_invoice_paid_webhook(
    session: AsyncSession,
    *,
    org_idx: int,
    subscription: Subscription,
    invoice_id: str,
    amount_paid: int = 2900,
) -> None:
    """Transition subscription to active via invoice.paid webhook path."""
    provider_event_id = f"evt_pl_{org_idx:04d}_paid"
    payload = _invoice_paid_payload(
        provider_event_id=provider_event_id,
        invoice_id=invoice_id,
        external_subscription_id=subscription.external_subscription_id or "",
        amount_paid=amount_paid,
    )
    webhook = await persist_webhook(
        session,
        provider_event_id=provider_event_id,
        event_type="invoice.paid",
        payload=payload,
    )
    if webhook is not None:
        await process_webhook(session, webhook.id)


async def apply_payment_failed_webhook(
    session: AsyncSession,
    *,
    org_idx: int,
    subscription: Subscription,
    event_kind: str = "failed",
) -> None:
    """Transition active subscription to past_due via invoice.payment_failed."""
    provider_event_id = f"evt_pl_{org_idx:04d}_{event_kind}"
    ext_id = subscription.external_subscription_id or ""
    payload = _payment_failed_payload(
        provider_event_id=provider_event_id,
        invoice_id=f"in_pl_{org_idx:04d}_{event_kind}",
        external_subscription_id=ext_id,
    )
    webhook = await persist_webhook(
        session,
        provider_event_id=provider_event_id,
        event_type="invoice.payment_failed",
        payload=payload,
    )
    if webhook is not None:
        await process_webhook(session, webhook.id)
