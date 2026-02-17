"""HTTP client for the mock-stripe service (implements PaymentProviderPort)."""

from __future__ import annotations

import httpx

from billing_platform.config import Settings, get_settings


class MockStripeClient:
    """PaymentProviderPort implementation backed by mock-stripe HTTP API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._base_url = self._settings.mock_stripe_base_url.rstrip("/")

    async def create_customer(self, *, organization_public_id: str, email: str) -> str:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30.0) as client:
            response = await client.post(
                "/v1/customers",
                json={
                    "organization_public_id": organization_public_id,
                    "email": email,
                },
            )
            response.raise_for_status()
            body = response.json()
            return str(body["id"])

    async def create_subscription(
        self,
        *,
        customer_id: str,
        price_id: str,
        trial_days: int,
    ) -> str:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30.0) as client:
            response = await client.post(
                "/v1/subscriptions",
                json={
                    "customer_id": customer_id,
                    "price_id": price_id,
                    "trial_days": trial_days,
                },
            )
            response.raise_for_status()
            body = response.json()
            return str(body["id"])

    async def cancel_subscription(self, *, external_subscription_id: str) -> None:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30.0) as client:
            response = await client.post(f"/v1/subscriptions/{external_subscription_id}/cancel")
            response.raise_for_status()

    async def create_invoice(
        self,
        *,
        customer_id: str,
        amount_cents: int,
        currency: str,
        idempotency_key: str,
    ) -> str:
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30.0) as client:
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
            body = response.json()
            return str(body["id"])

    async def list_invoices(self) -> list[dict[str, object]]:
        """Fetch all invoices from the mock Stripe registry."""
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30.0) as client:
            response = await client.get("/v1/invoices")
            response.raise_for_status()
            body = response.json()
            data = body.get("data")
            if not isinstance(data, list):
                return []
            return [item for item in data if isinstance(item, dict)]

    async def retry_invoice_payment(self, *, invoice_id: str) -> dict[str, object]:
        """Trigger a mock payment retry for an open invoice."""
        async with httpx.AsyncClient(base_url=self._base_url, timeout=30.0) as client:
            response = await client.post(f"/v1/invoices/{invoice_id}/retry")
            response.raise_for_status()
            body = response.json()
            return body if isinstance(body, dict) else {}
