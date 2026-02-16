from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from billing_platform.api.openapi_docs import APP_DESCRIPTION, APP_SUMMARY, SERVERS
from billing_platform.api.v1.admin.api_keys import router as api_keys_admin_router
from billing_platform.api.v1.admin.dunning import router as dunning_router
from billing_platform.api.v1.admin.reconciliation import router as reconciliation_router
from billing_platform.api.v1.admin.webhooks import router as webhooks_admin_router
from billing_platform.api.v1.catalog import router as catalog_router
from billing_platform.api.v1.entitlements import router as entitlements_router
from billing_platform.api.v1.health import router as health_router
from billing_platform.api.v1.invoices import router as invoices_router
from billing_platform.api.v1.ledger import router as ledger_router
from billing_platform.api.v1.organizations import router as organizations_router
from billing_platform.api.v1.subscriptions import router as subscriptions_router
from billing_platform.api.v1.usage import router as usage_router
from billing_platform.api.v1.webhooks import router as webhooks_router
from billing_platform.config import get_settings
from billing_platform.db import close_db_engine
from billing_platform.integrations.redis_cache import close_redis_client, get_redis_client
from billing_platform.logging import configure_logging, get_logger
from billing_platform.middleware.request_context import RequestContextMiddleware
from billing_platform.telemetry import configure_telemetry

logger = get_logger(__name__)

OPENAPI_TAGS = [
    {"name": "organizations", "description": "Tenant organizations and API key issuance."},
    {"name": "catalog", "description": "Products, plans, prices, features, and snapshots."},
    {"name": "subscriptions", "description": "Subscription lifecycle and plan changes."},
    {"name": "entitlements", "description": "Entitlement evaluation and cache invalidation."},
    {"name": "ledger", "description": "Financial ledger entries (read-only)."},
    {"name": "invoices", "description": "Invoice listing and detail (read-only)."},
    {"name": "usage", "description": "Usage event ingestion and aggregate reads."},
    {"name": "webhooks", "description": "Provider webhook ingestion (HMAC auth)."},
    {"name": "health", "description": "Liveness and readiness probes."},
    {"name": "api-keys", "description": "API key rotation and revocation."},
    {"name": "dunning", "description": "Dunning campaign management."},
    {"name": "reconciliation", "description": "Ledger vs provider reconciliation runs."},
    {"name": "webhooks-admin", "description": "Webhook replay for failed events."},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging()
    await get_redis_client()
    logger.info("application_startup", shutdown_grace_seconds=settings.shutdown_grace_seconds)
    yield
    await close_redis_client()
    await close_db_engine()
    logger.info(
        "application_shutdown",
        shutdown_grace_seconds=settings.shutdown_grace_seconds,
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="Billing Platform",
        summary=APP_SUMMARY,
        description=APP_DESCRIPTION,
        servers=SERVERS,
        lifespan=lifespan,
        openapi_tags=OPENAPI_TAGS,
    )

    app.include_router(organizations_router, prefix="/v1")
    app.include_router(catalog_router, prefix="/v1")
    app.include_router(subscriptions_router, prefix="/v1")
    app.include_router(entitlements_router, prefix="/v1")
    app.include_router(ledger_router, prefix="/v1")
    app.include_router(invoices_router, prefix="/v1")
    app.include_router(usage_router, prefix="/v1")
    app.include_router(webhooks_router, prefix="/v1")
    app.include_router(reconciliation_router, prefix="/v1")
    app.include_router(api_keys_admin_router, prefix="/v1")
    app.include_router(dunning_router, prefix="/v1")
    app.include_router(webhooks_admin_router, prefix="/v1")
    app.include_router(health_router)

    configure_telemetry(app, service_name="billing-api")
    app.add_middleware(RequestContextMiddleware)

    return app


app = create_app()
