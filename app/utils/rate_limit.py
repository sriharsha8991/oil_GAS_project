"""Redis-backed token-bucket rate limiter.

Used by `POST /support/tickets` to cap submissions per user per hour and
per day. Falls open on Redis errors (logged) — never blocks legitimate
traffic if Redis is down.

Returns the seconds the caller should retry after when the limit is hit.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import redis.asyncio as aioredis

from app.utils.logging import get_logger

logger = get_logger(__name__)

_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/2")
_client_singleton: aioredis.Redis | None = None


def _client() -> aioredis.Redis:
    global _client_singleton  # noqa: PLW0603
    if _client_singleton is None:
        _client_singleton = aioredis.from_url(_REDIS_URL, decode_responses=True)
    return _client_singleton


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    retry_after_seconds: int = 0
    scope: str = ""


async def check_and_increment(
    *,
    key: str,
    limit: int,
    window_seconds: int,
) -> RateLimitDecision:
    """Atomic INCR + EXPIRE; returns whether the request fits in the window."""
    if limit <= 0:
        return RateLimitDecision(allowed=True)
    try:
        client = _client()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = await pipe.execute()
        count = int(count)
        ttl = int(ttl)
        if ttl < 0:
            await client.expire(key, window_seconds)
            ttl = window_seconds
        if count > limit:
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=max(ttl, 1),
                scope=key,
            )
        return RateLimitDecision(allowed=True)
    except Exception as exc:  # noqa: BLE001
        # Fail open — log and let the request through.
        logger.warning("rate_limit_check_failed", key=key, error=str(exc))
        return RateLimitDecision(allowed=True)


async def enforce_support_submission(
    *,
    user_id: str,
    per_hour: int,
    per_day: int,
) -> RateLimitDecision:
    """Check both hour and day buckets for support ticket submissions."""
    hour = await check_and_increment(
        key=f"rl:support:hour:{user_id}",
        limit=per_hour,
        window_seconds=3600,
    )
    if not hour.allowed:
        return hour
    day = await check_and_increment(
        key=f"rl:support:day:{user_id}",
        limit=per_day,
        window_seconds=86400,
    )
    return day
