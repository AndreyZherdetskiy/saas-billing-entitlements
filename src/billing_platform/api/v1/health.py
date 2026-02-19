"""Liveness and readiness probes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from aiokafka import AIOKafkaClient
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from billing_platform.api.openapi_docs import SERVICE_UNAVAILABLE_RESPONSE
from billing_platform.config import Settings, get_settings
from billing_platform.db.session import get_engine
from billing_platform.integrations.redis_cache import get_redis_client

router = APIRouter(tags=["health"])

ReadyState = Literal["ok", "degraded", "unavailable"]
CheckState = Literal["ok", "fail"]


class HealthLiveResponse(BaseModel):
    """Liveness probe response."""

    status: str = Field(description="Process liveness status; ok when the API responds.")


class ReadyChecksResponse(BaseModel):
    """Per-dependency readiness check results."""

    postgres: CheckState = Field(description="PostgreSQL connectivity check result.")
    redis: CheckState = Field(description="Redis connectivity check result.")
    kafka: CheckState = Field(description="Kafka connectivity check result.")


class ReadyResponse(BaseModel):
    """Readiness probe response."""

    status: ReadyState = Field(
        description="Aggregate readiness: ok, degraded, or unavailable.",
    )
    reasons: list[str] = Field(
        description="Failure reasons when any dependency check failed.",
    )
    checks: ReadyChecksResponse = Field(description="Per-dependency check results.")


@dataclass(frozen=True)
class ReadyStatus:
    """Aggregate readiness of PostgreSQL, Redis, and Kafka."""

    status: ReadyState
    reasons: list[str]
    checks: dict[str, CheckState]

    def to_response(self) -> ReadyResponse:
        return ReadyResponse(
            status=self.status,
            reasons=self.reasons,
            checks=ReadyChecksResponse(
                postgres=self.checks.get("postgres", "fail"),
                redis=self.checks.get("redis", "fail"),
                kafka=self.checks.get("kafka", "fail"),
            ),
        )


async def _check_postgres() -> tuple[bool, str | None]:
    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:
        return False, f"postgres: {exc}"


async def _check_redis() -> tuple[bool, str | None]:
    try:
        client = await get_redis_client()
        await client.ping()
        return True, None
    except Exception as exc:
        return False, f"redis: {exc}"


async def _check_kafka(bootstrap_servers: str) -> tuple[bool, str | None]:
    client = AIOKafkaClient(bootstrap_servers=bootstrap_servers)
    try:
        await client.bootstrap()
        return True, None
    except Exception as exc:
        return False, f"kafka: {exc}"
    finally:
        await client.close()


async def check_ready(settings: Settings) -> ReadyStatus:
    """Ping PostgreSQL, Redis, and Kafka; classify ok | degraded | unavailable."""
    reasons: list[str] = []
    checks: dict[str, CheckState] = {}

    postgres_ok, postgres_reason = await _check_postgres()
    checks["postgres"] = "ok" if postgres_ok else "fail"
    if postgres_reason:
        reasons.append(postgres_reason)

    redis_ok, redis_reason = await _check_redis()
    checks["redis"] = "ok" if redis_ok else "fail"
    if redis_reason:
        reasons.append(redis_reason)

    kafka_ok, kafka_reason = await _check_kafka(settings.kafka_bootstrap_servers)
    checks["kafka"] = "ok" if kafka_ok else "fail"
    if kafka_reason:
        reasons.append(kafka_reason)

    if not postgres_ok or not redis_ok:
        return ReadyStatus(status="unavailable", reasons=reasons, checks=checks)

    if not kafka_ok:
        if settings.health_kafka_optional:
            return ReadyStatus(status="degraded", reasons=reasons, checks=checks)
        return ReadyStatus(status="unavailable", reasons=reasons, checks=checks)

    return ReadyStatus(status="ok", reasons=[], checks=checks)


@router.get(
    "/health/live",
    response_model=HealthLiveResponse,
    summary="Liveness probe",
    description=(
        "Returns 200 when the API process is running. Public probe with no authentication "
        "and no dependency checks — use for Kubernetes liveness."
    ),
)
async def live() -> HealthLiveResponse:
    """Liveness — process is up; no dependency checks."""
    return HealthLiveResponse(status="ok")


@router.get(
    "/health/ready",
    response_model=ReadyResponse,
    summary="Readiness probe",
    description=(
        "Checks PostgreSQL, Redis, and Kafka connectivity. Public probe with no authentication. "
        "Returns 503 when unavailable; returns 200 with status degraded when Kafka is optional "
        "and unreachable."
    ),
    responses=SERVICE_UNAVAILABLE_RESPONSE,
)
async def ready() -> JSONResponse:
    """Readiness — PostgreSQL + Redis + Kafka must be reachable.

    When ``HEALTH_KAFKA_OPTIONAL=true`` (see ``config.health_kafka_optional``),
    a Kafka check failure yields HTTP 200 with ``status: degraded`` instead of 503.
    See README «Health» and repo-root ``.env.example``.
    """
    result = await check_ready(get_settings())
    body = result.to_response()
    status_code = 503 if result.status == "unavailable" else 200
    return JSONResponse(status_code=status_code, content=body.model_dump())
