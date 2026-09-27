"""Small JSON cache abstraction.

Redis is used when configured and reachable; otherwise caching is a no-op so
the API keeps working (Redis is an optimisation, never a hard dependency).

Invalidation uses a namespace version: writes bump `ns:<name>:v`, which makes
every previously cached key in that namespace unreachable (they expire by TTL).
"""

import json
import logging
import time
from typing import Any, Protocol

import redis

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class Cache(Protocol):
    def get(self, key: str) -> Any | None: ...
    def set(self, key: str, value: Any, ttl: int) -> None: ...
    def incr(self, key: str, ttl: int) -> int: ...
    def namespace_version(self, namespace: str) -> int: ...
    def bump_namespace(self, namespace: str) -> None: ...


class NullCache:
    def get(self, key: str) -> Any | None:
        return None

    def set(self, key: str, value: Any, ttl: int) -> None:
        pass

    def incr(self, key: str, ttl: int) -> int:
        return 0

    def namespace_version(self, namespace: str) -> int:
        return 0

    def bump_namespace(self, namespace: str) -> None:
        pass


class InMemoryCache:
    """Single-process cache; used in tests and as a rate-limit fallback."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float]] = {}

    def _alive(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None or item[1] < time.monotonic():
            self._data.pop(key, None)
            return None
        return item[0]

    def get(self, key: str) -> Any | None:
        return self._alive(key)

    def set(self, key: str, value: Any, ttl: int) -> None:
        self._data[key] = (value, time.monotonic() + ttl)

    def incr(self, key: str, ttl: int) -> int:
        current = self._alive(key)
        if current is None:
            self._data[key] = (1, time.monotonic() + ttl)
            return 1
        value, expires = self._data[key]
        self._data[key] = (value + 1, expires)
        return value + 1

    def namespace_version(self, namespace: str) -> int:
        return self._alive(f"ns:{namespace}:v") or 0

    def bump_namespace(self, namespace: str) -> None:
        self.set(f"ns:{namespace}:v", self.namespace_version(namespace) + 1, 86400)

    def clear(self) -> None:
        self._data.clear()


class RedisCache:
    def __init__(self, url: str) -> None:
        self._client = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5)

    def get(self, key: str) -> Any | None:
        try:
            raw = self._client.get(key)
        except redis.RedisError:
            logger.warning("cache_unavailable", extra={"op": "get"})
            return None
        return json.loads(raw) if raw is not None else None

    def set(self, key: str, value: Any, ttl: int) -> None:
        try:
            self._client.set(key, json.dumps(value, default=str), ex=ttl)
        except redis.RedisError:
            logger.warning("cache_unavailable", extra={"op": "set"})

    def incr(self, key: str, ttl: int) -> int:
        try:
            pipe = self._client.pipeline()
            pipe.incr(key)
            pipe.expire(key, ttl, nx=True)
            count, _ = pipe.execute()
            return int(count)
        except redis.RedisError:
            logger.warning("cache_unavailable", extra={"op": "incr"})
            return 0  # fail open: never block users because Redis is down

    def namespace_version(self, namespace: str) -> int:
        try:
            return int(self._client.get(f"ns:{namespace}:v") or 0)
        except redis.RedisError:
            return 0

    def bump_namespace(self, namespace: str) -> None:
        try:
            self._client.incr(f"ns:{namespace}:v")
        except redis.RedisError:
            logger.warning("cache_unavailable", extra={"op": "bump_namespace"})


_cache: Cache | None = None


def get_cache() -> Cache:
    global _cache
    if _cache is None:
        url = get_settings().redis_url
        _cache = RedisCache(url) if url else InMemoryCache()
    return _cache


def set_cache(cache: Cache) -> None:
    global _cache
    _cache = cache
