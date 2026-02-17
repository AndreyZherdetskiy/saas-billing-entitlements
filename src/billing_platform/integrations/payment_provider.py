"""Payment provider port (ADR-005)."""

from __future__ import annotations

from typing import Protocol


class PaymentProviderPort(Protocol):
    """Abstract payment provider; domain depends only on this protocol."""

    async def create_customer(self, *, organization_public_id: str, email: str) -> str:
        """Create a customer at the provider; return external customer id."""
        ...

    async def create_subscription(
        self,
        *,
        customer_id: str,
        price_id: str,
        trial_days: int,
    ) -> str:
        """Create a subscription; return external subscription id."""
        ...

    async def cancel_subscription(self, *, external_subscription_id: str) -> None:
        """Cancel a subscription at the provider."""
        ...

    async def create_invoice(
        self,
        *,
        customer_id: str,
        amount_cents: int,
        currency: str,
        idempotency_key: str,
    ) -> str:
        """Create an invoice at the provider; return external invoice id."""
        ...

    async def list_invoices(self) -> list[dict[str, object]]:
        """List invoices at the provider for reconciliation."""
        ...

    async def retry_invoice_payment(self, *, invoice_id: str) -> dict[str, object]:
        """Retry payment for an open invoice (dunning)."""
        ...
