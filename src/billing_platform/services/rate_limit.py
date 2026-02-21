"""Redis fixed-window API key rate limiting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from redis.asyncio import Redis

from billing_platform.config import Settings
from billing_platform.domain.models.api_key import ApiKeyRole

WINDOW_SECONDS = 60

_INCR_WITH_EXPIRE_LUA = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return count
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Outcome of a fixed-window rate limit check."""

    allowed: bool
    remaining: int
    retry_after_seconds: int | None


def _stable_api_key_id(api_key_id: int | str | UUID) -> str:
    if isinstance(api_key_id, UUID):
        return str(api_key_id)
    return str(api_key_id)


def rate_limit_window_key(api_key_id: int | str | UUID, *, now: datetime | None = None) -> str:
    """Build Redis key for the current one-minute fixed window."""
    moment = now or datetime.now(UTC)
    window = moment.strftime("%Y%m%d%H%M")
    return f"rl:api_key:{_stable_api_key_id(api_key_id)}:{window}"


async def check_rate_limit(
    redis: Redis,
    *,
    api_key_id: int | str | UUID,
    limit_per_minute: int,
    now: datetime | None = None,
) -> RateLimitDecision:
    """Increment the per-minute counter and return allow/deny with Retry-After hint."""
    if limit_per_minute <= 0:
        return RateLimitDecision(
            allowed=True,
            remaining=limit_per_minute,
            retry_after_seconds=None,
        )

    key = rate_limit_window_key(api_key_id, now=now)
    raw_count = await redis.eval(
        _INCR_WITH_EXPIRE_LUA,
        1,
        key,
        str(WINDOW_SECONDS),
    )
    count = int(raw_count)
    allowed = count <= limit_per_minute
    remaining = max(0, limit_per_minute - count) if allowed else 0
    retry_after_seconds: int | None = None
    if not allowed:
        ttl = await redis.ttl(key)
        retry_after_seconds = max(1, int(ttl)) if ttl and ttl > 0 else WINDOW_SECONDS
    return RateLimitDecision(
        allowed=allowed,
        remaining=remaining,
        retry_after_seconds=retry_after_seconds,
    )


def rate_limit_headers(decision: RateLimitDecision, *, limit_per_minute: int) -> dict[str, str]:
    """Optional response headers for successful rate-limited requests."""
    headers: dict[str, str] = {
        "X-RateLimit-Limit": str(limit_per_minute),
        "X-RateLimit-Remaining": str(decision.remaining),
    }
    if decision.retry_after_seconds is not None:
        headers["Retry-After"] = str(decision.retry_after_seconds)
    return headers


def resolve_api_rate_limit_per_minute(*, role: str, settings: Settings) -> int:
    """Return per-minute limit for the authenticated API key role."""
    if role == ApiKeyRole.PLATFORM_ADMIN.value:
        return settings.api_rate_limit_platform_admin_per_minute
    return settings.api_rate_limit_per_minute
