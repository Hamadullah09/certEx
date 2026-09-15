"""Redis-backed sliding-window rate limiting.

A fixed window lets a caller fire 2x the quota across a window boundary. This uses
a sorted set per key holding one member per request timestamp, trimmed to the
window on every call, which gives a true rolling limit.

The check, trim and insert run inside one Lua script so concurrent workers cannot
interleave between reading the count and recording the request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from functools import lru_cache

import redis.asyncio as aioredis
from redis.asyncio.client import Redis
from redis.exceptions import RedisError

from certex.config import Settings, get_settings
from certex.core.errors import RateLimitedError
from certex.logging_setup import get_logger, safe_error

__all__ = [
    "RateLimitDecision",
    "RateLimiter",
    "close_rate_limiter",
    "get_rate_limiter",
]

logger = get_logger(__name__)

# KEYS[1] = bucket key
# ARGV[1] = now (ms), ARGV[2] = window (ms), ARGV[3] = limit, ARGV[4] = member id
# Returns {allowed, count, reset_after_ms}
_SLIDING_WINDOW_LUA = """
local key    = KEYS[1]
local now    = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit  = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)

if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local reset = window
  if oldest[2] then
    reset = (tonumber(oldest[2]) + window) - now
  end
  if reset < 0 then reset = 0 end
  return {0, count, reset}
end

redis.call('ZADD', key, now, member)
redis.call('PEXPIRE', key, window)
return {1, count + 1, window}
"""


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    retry_after_seconds: int

    def raise_if_denied(self, *, what: str) -> None:
        if self.allowed:
            return
        raise RateLimitedError(
            f"Rate limit of {self.limit} {what} per minute exceeded.",
            remediation=(
                f"Wait {self.retry_after_seconds}s and retry. "
                "Raise the limit in settings if this is expected volume."
            ),
            retry_after_seconds=self.retry_after_seconds,
        )


class RateLimiter:
    """Sliding-window limiter.

    Fails **open**: if Redis is unreachable the request proceeds and a warning is
    logged. Availability of the extraction pipeline matters more than throttle
    precision, and an outage must not lock every operator out of their own data.
    """

    def __init__(self, client: Redis, *, settings: Settings) -> None:
        self._client = client
        self._settings = settings
        self._script = client.register_script(_SLIDING_WINDOW_LUA)

    async def check(
        self,
        bucket: str,
        identity: str,
        *,
        limit: int,
        window_seconds: int = 60,
    ) -> RateLimitDecision:
        if not self._settings.rate_limit_enabled:
            return RateLimitDecision(True, limit, limit, 0)

        now_ms = int(time.time() * 1000)
        key = f"ratelimit:{bucket}:{identity}"
        member = f"{now_ms}-{time.monotonic_ns()}"

        try:
            raw = await self._script(
                keys=[key],
                args=[now_ms, window_seconds * 1000, limit, member],
            )
        except RedisError as exc:
            logger.warning("ratelimit.unavailable", error_type=safe_error(exc), queue=bucket)
            return RateLimitDecision(True, limit, limit, 0)

        allowed = bool(int(raw[0]))
        count = int(raw[1])
        reset_ms = int(raw[2])
        return RateLimitDecision(
            allowed=allowed,
            limit=limit,
            remaining=max(limit - count, 0),
            retry_after_seconds=max(1, (reset_ms + 999) // 1000) if not allowed else 0,
        )

    async def close(self) -> None:
        await self._client.aclose()


@lru_cache(maxsize=1)
def get_rate_limiter() -> RateLimiter:
    settings = get_settings()
    client: Redis = aioredis.from_url(  # type: ignore[no-untyped-call]
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
        health_check_interval=30,
    )
    return RateLimiter(client, settings=settings)


async def close_rate_limiter() -> None:
    limiter = get_rate_limiter()
    await limiter.close()
    get_rate_limiter.cache_clear()
