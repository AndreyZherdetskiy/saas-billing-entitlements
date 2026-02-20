"""Shared OpenAPI metadata, error schemas, and request examples."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

APP_SUMMARY = "Multi-tenant billing, subscriptions, entitlements, and usage API."

APP_DESCRIPTION = """
Partner-facing REST API for organizations, catalog, subscriptions, entitlements, usage,
invoices, ledger, and webhook ingestion.

## Identifiers

All public resources are identified by **UUIDv7** (`public_id` or resource-specific external id).
Sequential internal database ids are never exposed in API requests or responses.

## Authentication

Send `Authorization: Bearer <api_key>` on authenticated routes. API keys are issued per
organization or as platform-wide admin keys.

## Idempotency

Mutating routes that accept `Idempotency-Key` treat repeated keys as safe retries and return
the original result without duplicating side effects.

## API groups

- **organizations** — tenant profiles and API key issuance
- **catalog** — products, plans, prices, features, snapshots
- **subscriptions** — lifecycle and plan changes
- **entitlements** — evaluation and cache invalidation
- **usage** — metered event ingestion and aggregates
- **invoices** — invoice listing and detail (read-only)
- **ledger** — append-only financial entries (read-only)
- **webhooks** — provider webhook ingestion (HMAC auth)
- **admin** — API key rotation, dunning, reconciliation, webhook replay
- **health** — liveness and readiness probes
"""

SERVERS = [{"url": "http://localhost:8000", "description": "Local Compose"}]

DEMO_ORG_PUBLIC_ID = "01900000-0000-7000-8000-000000000001"
DEMO_PLAN_PUBLIC_ID = "01900000-0000-7000-8000-000000000010"


class ErrorResponse(BaseModel):
    """Standard HTTP error payload."""

    detail: str = Field(description="Human-readable error message.")


# FastAPI path-operation `responses=` is `dict[int | str, dict[str, Any]] | None`
# (https://fastapi.tiangolo.com/reference/apirouter/).
type OpenAPIResponses = dict[int | str, dict[str, Any]]

UNAUTHORIZED_RESPONSE: OpenAPIResponses = {
    401: {
        "description": "Missing or invalid API key.",
        "model": ErrorResponse,
    },
}

FORBIDDEN_RESPONSE: OpenAPIResponses = {
    403: {
        "description": "Insufficient permissions for this operation.",
        "model": ErrorResponse,
    },
}

NOT_FOUND_RESPONSE: OpenAPIResponses = {
    404: {
        "description": "Requested resource was not found.",
        "model": ErrorResponse,
    },
}

BAD_REQUEST_RESPONSE: OpenAPIResponses = {
    400: {
        "description": "Invalid request parameters or payload.",
        "model": ErrorResponse,
    },
}

CONFLICT_RESPONSE: OpenAPIResponses = {
    409: {
        "description": "Request conflicts with the current resource state.",
        "model": ErrorResponse,
    },
}

SERVICE_UNAVAILABLE_RESPONSE: OpenAPIResponses = {
    503: {
        "description": "A required dependency is unavailable.",
        "model": ErrorResponse,
    },
}


def merge_responses(*parts: OpenAPIResponses) -> OpenAPIResponses:
    merged: OpenAPIResponses = {}
    for part in parts:
        merged.update(part)
    return merged


AUTH_RESPONSES = merge_responses(UNAUTHORIZED_RESPONSE, FORBIDDEN_RESPONSE)

CREATE_ORGANIZATION_EXAMPLES = {
    "demo_tenant": {
        "summary": "Demo tenant organization",
        "description": "Organization aligned with local Compose demo seed keys.",
        "value": {
            "name": "Acme Corp",
            "external_id": "ext_acme_001",
            "billing_email": "billing@acme.example",
            "metadata": {"tier": "startup"},
        },
    },
}

CREATE_SUBSCRIPTION_EXAMPLES = {
    "starter_plan": {
        "summary": "Subscribe demo org to starter plan",
        "description": "Uses demo organization public id and a representative plan UUIDv7.",
        "value": {
            "organization_public_id": DEMO_ORG_PUBLIC_ID,
            "plan_id": DEMO_PLAN_PUBLIC_ID,
            "metadata": {},
        },
    },
}

EVALUATE_ENTITLEMENTS_EXAMPLES = {
    "api_calls_quota": {
        "summary": "Check api_calls quota",
        "description": "Single quota check for the demo api_calls feature.",
        "value": {
            "organization_public_id": DEMO_ORG_PUBLIC_ID,
            "checks": [{"feature_key": "api_calls", "quantity": 1}],
        },
    },
}

USAGE_BATCH_EXAMPLES = {
    "single_event": {
        "summary": "Record one api_calls event",
        "description": "Batch ingest with deduplication via per-event idempotency_key.",
        "value": {
            "organization_public_id": DEMO_ORG_PUBLIC_ID,
            "events": [
                {
                    "feature_key": "api_calls",
                    "quantity": 1,
                    "idempotency_key": "evt_demo_001",
                },
            ],
        },
    },
}
