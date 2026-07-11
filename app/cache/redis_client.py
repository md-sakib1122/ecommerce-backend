"""Redis connection + JSON cache helpers for the category tree.

The category tree is expensive to assemble (a full DFS over the adjacency-list
`categories` table), so `CategoryService` caches the rendered tree here and
invalidates it on any category write.

Redis is an **optional** dependency: the app fails fast on the database at
startup, but never on Redis. Every helper therefore degrades gracefully — on a
connection error or miss it behaves as a cache miss / no-op and logs a warning,
so the API keeps serving straight from the database when Redis is unavailable.
"""
import json
import logging
from typing import Any

import redis.asyncio as redis

from app.core.config import settings

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    """Return the lazily-created module-level async Redis client."""
    global _client
    if _client is None:
        _client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _client


async def cache_get_json(key: str) -> Any | None:
    """Return the decoded JSON value at `key`, or None on a miss or any error."""
    try:
        raw = await get_client().get(key)
    except Exception as exc:  # pragma: no cover - depends on Redis availability
        logger.warning("Redis GET failed for %s: %s", key, exc)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.warning("Cached value for %s is not valid JSON: %s", key, exc)
        return None


async def cache_set_json(key: str, value: Any, ttl: int) -> None:
    """Store `value` as JSON at `key` with a `ttl`-second expiry. Best-effort."""
    try:
        await get_client().set(key, json.dumps(value), ex=ttl)
    except Exception as exc:  # pragma: no cover - depends on Redis availability
        logger.warning("Redis SET failed for %s: %s", key, exc)


async def cache_delete_pattern(pattern: str) -> None:
    """Delete every key matching `pattern` (e.g. ``category:tree*``). Best-effort."""
    try:
        client = get_client()
        keys = [key async for key in client.scan_iter(match=pattern)]
        if keys:
            await client.delete(*keys)
    except Exception as exc:  # pragma: no cover - depends on Redis availability
        logger.warning("Redis DELETE failed for pattern %s: %s", pattern, exc)


async def close_redis() -> None:
    """Close the client (optional; call from app shutdown)."""
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception as exc:  # pragma: no cover
            logger.warning("Redis close failed: %s", exc)
        _client = None
