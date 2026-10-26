"""Shared async Redis client."""

import redis.asyncio as aioredis

from app.core.config import get_settings

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        # protocol=2: stay compatible with older Redis servers (e.g. Windows ports < 6)
        # as well as Redis 7 in Docker. Nothing here needs RESP3.
        # socket_timeout=None: newer redis-py builds default to 5s, which would
        # poison long-lived pub/sub reads; explicit per-call timeouts still apply.
        _client = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            protocol=2,
            socket_timeout=None,
        )
    return _client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
