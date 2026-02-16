"""Request-scoped structlog context: correlation_id and organization_id."""

from __future__ import annotations

import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from billing_platform.logging import PROBE_PATHS, get_logger

CORRELATION_ID_HEADER = "X-Correlation-ID"

logger = get_logger(__name__)


def bind_organization_id(organization_id: str) -> None:
    """Bind tenant organization public_id (or internal id) for request logs."""
    structlog.contextvars.bind_contextvars(organization_id=organization_id)


def restore_organization_context_from_request(request: Request) -> None:
    """Re-bind organization_id after call_next (BaseHTTPMiddleware isolates contextvars)."""
    org_id = getattr(request.state, "organization_id", None)
    if org_id is not None:
        bind_organization_id(org_id)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind correlation_id per request; log completion with duration_ms."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        correlation_id = request.headers.get(CORRELATION_ID_HEADER) or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)

        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            restore_organization_context_from_request(request)
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            logger.exception(
                "request_failed",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.clear_contextvars()
            raise

        restore_organization_context_from_request(request)
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        if request.url.path not in PROBE_PATHS:
            logger.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration_ms,
            )
        structlog.contextvars.clear_contextvars()

        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response
