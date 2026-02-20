"""Unit tests for correlation_id middleware, structlog context, and OpenTelemetry spans."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from billing_platform.config import get_settings
from billing_platform.db import get_read_session, get_session
from billing_platform.domain.models.api_key import ApiKeyRole
from billing_platform.logging import SkipProbeAccessFilter, configure_logging
from billing_platform.main import create_app
from billing_platform.middleware.request_context import CORRELATION_ID_HEADER
from billing_platform.services.api_keys import create_api_key
from billing_platform.services.organizations import create_organization
from billing_platform.telemetry import configure_telemetry


@pytest.fixture
def otel_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable OTel for observability tests (disabled globally in conftest)."""
    monkeypatch.setenv("OTEL_SDK_DISABLED", "false")
    get_settings.cache_clear()


def _parse_json_logs(stdout: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in stdout.splitlines() if line.strip().startswith("{")]


def _create_test_app(span_exporter: InMemorySpanExporter | None = None):
    configure_logging()
    with patch("billing_platform.main.configure_telemetry"):
        app = create_app()
    if span_exporter is not None:
        configure_telemetry(app, span_exporter=span_exporter)
    return app


@pytest.mark.asyncio
async def test_correlation_id_generated_when_header_missing(
    otel_enabled: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exporter = InMemorySpanExporter()
    app = _create_test_app(span_exporter=exporter)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    correlation_id = response.headers.get(CORRELATION_ID_HEADER)
    assert correlation_id is not None
    uuid.UUID(correlation_id)

    log_lines = _parse_json_logs(capsys.readouterr().out)
    health_completions = [
        entry for entry in log_lines if entry.get("event") == "request_completed"
    ]
    assert health_completions == []

    spans = exporter.get_finished_spans()
    # /health/live is excluded from instrumentation; correlation header still set.
    assert len(spans) == 0

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        documented = await client.get("/openapi.json")
    assert documented.status_code == 200
    completion_logs = [
        entry
        for entry in _parse_json_logs(capsys.readouterr().out)
        if entry.get("event") == "request_completed"
    ]
    assert len(completion_logs) == 1
    assert completion_logs[0]["correlation_id"]
    assert "duration_ms" in completion_logs[0]


@pytest.mark.asyncio
async def test_correlation_id_echoed_from_request_header(
    otel_enabled: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = _create_test_app()
    expected = "test-correlation-abc123"

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health/live",
            headers={CORRELATION_ID_HEADER: expected},
        )

    assert response.status_code == 200
    assert response.headers.get(CORRELATION_ID_HEADER) == expected

    log_lines = _parse_json_logs(capsys.readouterr().out)
    completion_logs = [entry for entry in log_lines if entry.get("event") == "request_completed"]
    assert completion_logs == []


@pytest.mark.asyncio
async def test_health_live_excluded_from_http_spans(otel_enabled: None) -> None:
    """Health probes are excluded from FastAPI instrumentation."""
    exporter = InMemorySpanExporter()
    app = _create_test_app(span_exporter=exporter)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")

    assert response.status_code == 200
    spans = exporter.get_finished_spans()
    assert len(spans) == 0


def test_skip_probe_access_filter_drops_health_lines() -> None:
    filt = SkipProbeAccessFilter()
    dropped = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '127.0.0.1:1 - "GET /health/ready HTTP/1.1" 200 OK',
        (),
        None,
    )
    kept = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '127.0.0.1:1 - "GET /v1/organizations HTTP/1.1" 200 OK',
        (),
        None,
    )
    assert filt.filter(dropped) is False
    assert filt.filter(kept) is True


@pytest.mark.asyncio
async def test_tenant_api_key_request_logs_organization_id(
    migrated_postgres_url: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tenant API-key auth binds organization public_id into request_completed structlog JSON."""
    monkeypatch.setenv("API_RATE_LIMIT_PER_MINUTE", "0")
    monkeypatch.setenv("API_RATE_LIMIT_PLATFORM_ADMIN_PER_MINUTE", "0")
    get_settings.cache_clear()

    org = await create_organization(
        db_session,
        name="Observability Org",
        external_id=f"ext-obs-{uuid.uuid4().hex[:8]}",
        idempotency_key=f"idem-obs-{uuid.uuid4().hex[:8]}",
    )
    _, raw_key = await create_api_key(
        db_session,
        organization_id=org.id,
        role=ApiKeyRole.PRODUCT_SERVICE.value,
    )
    await db_session.commit()

    engine = create_async_engine(migrated_postgres_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app = _create_test_app()
    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_read_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            f"/v1/organizations/{org.public_id}",
            headers={"Authorization": f"Bearer {raw_key}"},
        )

    await engine.dispose()
    get_settings.cache_clear()

    assert response.status_code == 200

    log_lines = _parse_json_logs(capsys.readouterr().out)
    completion_logs = [entry for entry in log_lines if entry.get("event") == "request_completed"]
    assert len(completion_logs) == 1
    assert completion_logs[0]["organization_id"] == str(org.public_id)
