"""Mock Stripe HTTP service: customers, subscriptions, invoices, signed webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

WEBHOOK_SECRET = os.environ.get("MOCK_STRIPE_WEBHOOK_SECRET") or ""
WEBHOOK_CALLBACK_URL = os.environ.get(
    "WEBHOOK_CALLBACK_URL",
    "http://billing-api:8000/v1/webhooks/mock-stripe",
)

app = FastAPI(title="Mock Stripe")

_customers: dict[str, dict[str, Any]] = {}
_subscriptions: dict[str, dict[str, Any]] = {}
_invoices: dict[str, dict[str, Any]] = {}
_invoice_idempotency: dict[str, str] = {}
_event_counter = 0


class CreateCustomerRequest(BaseModel):
    organization_public_id: str
    email: str


class CreateSubscriptionRequest(BaseModel):
    customer_id: str
    price_id: str
    trial_days: int = 0


def _next_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(8)}"


def _sign_payload(payload: bytes, secret: str, *, timestamp: int | None = None) -> str:
    ts = str(timestamp if timestamp is not None else int(time.time()))
    signed_payload = f"{ts}.".encode() + payload
    digest = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


async def _emit_webhook(event_type: str, data_object: dict[str, Any]) -> dict[str, Any]:
    global _event_counter  # noqa: PLW0603
    _event_counter += 1
    event = {
        "id": f"evt_{_event_counter:06d}_{secrets.token_hex(4)}",
        "object": "event",
        "type": event_type,
        "created": int(time.time()),
        "data": {"object": data_object},
    }
    payload = json.dumps(event, separators=(",", ":")).encode()
    signature = _sign_payload(payload, WEBHOOK_SECRET)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            WEBHOOK_CALLBACK_URL,
            content=payload,
            headers={
                "Content-Type": "application/json",
                "Stripe-Signature": signature,
            },
        )
        response.raise_for_status()
    return event


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/customers")
async def create_customer(body: CreateCustomerRequest) -> dict[str, Any]:
    customer_id = _next_id("cus")
    customer = {
        "id": customer_id,
        "object": "customer",
        "email": body.email,
        "metadata": {"organization_public_id": body.organization_public_id},
    }
    _customers[customer_id] = customer
    return customer


@app.post("/v1/subscriptions")
async def create_subscription(body: CreateSubscriptionRequest) -> dict[str, Any]:
    if body.customer_id not in _customers:
        raise HTTPException(status_code=404, detail="customer not found")

    subscription_id = _next_id("sub")
    invoice_id = _next_id("in")
    subscription = {
        "id": subscription_id,
        "object": "subscription",
        "customer": body.customer_id,
        "status": "trialing" if body.trial_days > 0 else "incomplete",
        "items": [{"price": body.price_id}],
        "trial_days": body.trial_days,
        "latest_invoice": invoice_id,
    }
    invoice = {
        "id": invoice_id,
        "object": "invoice",
        "customer": body.customer_id,
        "subscription": subscription_id,
        "status": "open",
        "amount_due": 1000,
        "currency": "usd",
    }
    _subscriptions[subscription_id] = subscription
    _invoices[invoice_id] = invoice
    return subscription


@app.post("/v1/subscriptions/{subscription_id}/cancel")
async def cancel_subscription(subscription_id: str) -> dict[str, Any]:
    subscription = _subscriptions.get(subscription_id)
    if subscription is None:
        raise HTTPException(status_code=404, detail="subscription not found")
    subscription["status"] = "canceled"
    subscription["canceled_at"] = int(time.time())
    await _emit_webhook("customer.subscription.deleted", subscription)
    return subscription


class CreateInvoiceRequest(BaseModel):
    customer_id: str
    amount_cents: int
    currency: str = "usd"
    idempotency_key: str


@app.post("/v1/invoices")
async def create_invoice(body: CreateInvoiceRequest) -> dict[str, Any]:
    if body.customer_id not in _customers:
        raise HTTPException(status_code=404, detail="customer not found")

    existing_id = _invoice_idempotency.get(body.idempotency_key)
    if existing_id is not None and existing_id in _invoices:
        return _invoices[existing_id]

    invoice_id = _next_id("in")
    invoice = {
        "id": invoice_id,
        "object": "invoice",
        "customer": body.customer_id,
        "status": "open",
        "amount_due": body.amount_cents,
        "currency": body.currency.lower(),
    }
    _invoices[invoice_id] = invoice
    _invoice_idempotency[body.idempotency_key] = invoice_id
    return invoice


@app.get("/v1/invoices")
async def list_invoices() -> dict[str, Any]:
    """Return all invoices in the mock registry (for reconciliation)."""
    return {"object": "list", "data": list(_invoices.values())}


@app.post("/v1/invoices/{invoice_id}/pay")
async def pay_invoice(invoice_id: str) -> dict[str, Any]:
    invoice = _invoices.get(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    invoice["status"] = "paid"
    invoice["paid"] = True
    subscription_id = invoice.get("subscription")
    if subscription_id and subscription_id in _subscriptions:
        _subscriptions[subscription_id]["status"] = "active"
    await _emit_webhook("invoice.paid", invoice)
    return invoice


@app.post("/v1/invoices/{invoice_id}/retry")
async def retry_invoice(invoice_id: str) -> dict[str, Any]:
    """Simulate a failed payment retry (invoice stays open)."""
    invoice = _invoices.get(invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    invoice["attempt_count"] = int(invoice.get("attempt_count", 0)) + 1
    return invoice


class EmitWebhookRequest(BaseModel):
    event_type: str = Field(default="invoice.paid")
    data: dict[str, Any] = Field(default_factory=dict)


class SeedInvoiceRequest(BaseModel):
    """Register or replace an invoice in the mock registry (demo / reconciliation seed)."""

    id: str
    status: str = "open"
    amount_due: int = 1000
    amount_paid: int | None = None
    currency: str = "usd"
    customer: str | None = None
    subscription: str | None = None


@app.post("/v1/test/seed-invoice")
async def seed_invoice(body: SeedInvoiceRequest) -> dict[str, Any]:
    """Test helper: upsert an invoice into the in-memory registry."""
    invoice: dict[str, Any] = {
        "id": body.id,
        "object": "invoice",
        "status": body.status,
        "amount_due": body.amount_due,
        "currency": body.currency,
    }
    if body.amount_paid is not None:
        invoice["amount_paid"] = body.amount_paid
    if body.customer is not None:
        invoice["customer"] = body.customer
    if body.subscription is not None:
        invoice["subscription"] = body.subscription
    if body.status == "paid":
        invoice["paid"] = True
    _invoices[body.id] = invoice
    return invoice


@app.post("/v1/test/emit-webhook")
async def emit_test_webhook(body: EmitWebhookRequest) -> dict[str, Any]:
    """Test helper: emit a signed webhook to the billing-api callback."""
    return await _emit_webhook(body.event_type, body.data)
