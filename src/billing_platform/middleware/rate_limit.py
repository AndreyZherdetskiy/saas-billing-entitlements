"""HTTP helpers for API key rate limiting (enforcement wired via auth dependency)."""

from __future__ import annotations

from fastapi import HTTPException, status
from redis.exceptions import RedisError

from billing_platform.integrations.redis_cache import get_redis_client
from billing_platform.logging import get_logger
from billing_platform.observability.metrics import increment_http_rate_limited
from billing_platform.services.rate_limit import check_rate_limit, rate_limit_headers

EXEMPT_PATHS = frozenset({"/health/live", "/health/ready"})

logger = get_logger(__name__)


def is_rate_limit_exempt(path: str) -> bool:
    """Return True for paths that must never be rate limited."""
    return path in EXEMPT_PATHS


async def enforce_rate_limit_for_api_key(
    *,
    api_key_id: object,
    limit_per_minute: int,
) -> dict[str, str] | None:
    """Check Redis fixed-window limit; return rate-limit headers or raise HTTP 429."""
    if limit_per_minute <= 0:
        return None

    try:
        redis = await get_redis_client()
        decision = await check_rate_limit(
            redis,
            api_key_id=api_key_id,  # type: ignore[arg-type]  # enforce_rate_limit passes AuthContext.api_key_id (UUID|None); check_rate_limit accepts int|str|UUID
            limit_per_minute=limit_per_minute,
        )
    except RedisError as exc:
        logger.warning("rate_limit_redis_unavailable", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="rate limiting temporarily unavailable",
        ) from exc

    if not decision.allowed:
        increment_http_rate_limited()
        retry_after = decision.retry_after_seconds or 60
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return rate_limit_headers(decision, limit_per_minute=limit_per_minute)
