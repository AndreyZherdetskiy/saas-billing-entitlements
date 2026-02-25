#!/usr/bin/env python3
"""Seed ledger↔mock Stripe amount mismatch — docs/runbooks/reconciliation-mismatch.md."""

from __future__ import annotations

import asyncio
import json
import sys

import httpx

from billing_platform.config import get_settings
from billing_platform.db import get_session_factory
from billing_platform.domain.models.ledger import LedgerEntryType
from billing_platform.services.ledger import LedgerService
from billing_platform.services.organizations import create_organization

SEED_INVOICE_ID = "in_recon_seed_mismatch"
STRIPE_AMOUNT_CENTS = 1000
PLATFORM_AMOUNT_CENTS = 900
ORG_IDEMPOTENCY_KEY = "seed-recon-mismatch-org"
LEDGER_IDEMPOTENCY_KEY = "seed-recon-mismatch-ledger"


async def _ensure_mock_stripe_invoice(settings) -> None:
    """Register invoice in mock Stripe in-memory registry via test emit helper."""
    base_url = settings.mock_stripe_base_url.rstrip("/")
    invoice_payload = {
        "id": SEED_INVOICE_ID,
        "object": "invoice",
        "status": "paid",
        "amount_due": STRIPE_AMOUNT_CENTS,
        "amount_paid": STRIPE_AMOUNT_CENTS,
        "currency": "usd",
        "subscription": "sub_recon_seed",
    }
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        health = await client.get("/health")
        health.raise_for_status()
        response = await client.post(
            "/v1/test/seed-invoice",
            json=invoice_payload,
        )
        if response.status_code == 404:
            # Fallback: emit webhook only seeds platform path; register via list workaround.
            emit = await client.post(
                "/v1/test/emit-webhook",
                json={"event_type": "invoice.paid", "data": invoice_payload},
            )
            emit.raise_for_status()
            print(
                "warning: mock-stripe /v1/test/seed-invoice not available; "
                "invoice registry may be empty until endpoint is deployed",
                file=sys.stderr,
            )
        else:
            response.raise_for_status()


async def _seed_platform_ledger() -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        org = await create_organization(
            session,
            name="Recon Seed Mismatch Org",
            external_id="ext-recon-seed-mismatch",
            idempotency_key=ORG_IDEMPOTENCY_KEY,
            billing_email="recon-seed@example.com",
        )
        await LedgerService.post(
            session,
            organization_id=org.id,
            entry_type=LedgerEntryType.invoice_paid.value,
            amount_cents=PLATFORM_AMOUNT_CENTS,
            currency="USD",
            idempotency_key=LEDGER_IDEMPOTENCY_KEY,
            correlation_id="seed-recon-mismatch",
            metadata={"invoice_external_id": SEED_INVOICE_ID},
        )
        await session.commit()
        print(
            json.dumps(
                {
                    "organization_public_id": str(org.public_id),
                    "invoice_external_id": SEED_INVOICE_ID,
                    "stripe_amount_cents": STRIPE_AMOUNT_CENTS,
                    "platform_amount_cents": PLATFORM_AMOUNT_CENTS,
                    "delta_cents": STRIPE_AMOUNT_CENTS - PLATFORM_AMOUNT_CENTS,
                },
                indent=2,
            )
        )


async def main() -> None:
    settings = get_settings()
    await _ensure_mock_stripe_invoice(settings)
    await _seed_platform_ledger()
    print(
        f"Seeded mismatch: Stripe {STRIPE_AMOUNT_CENTS}c vs platform {PLATFORM_AMOUNT_CENTS}c "
        f"for invoice {SEED_INVOICE_ID}. Run POST /v1/admin/reconciliation/run to detect."
    )


if __name__ == "__main__":
    asyncio.run(main())
