"""A small cache for the analytics responses: Redis in production, memory for local work and tests, nothing when switched off.

The cache only ever makes things faster. If Redis is down or slow the answer is computed as if it were not there; a failure to
read or write is logged and never reaches the caller.
"""

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from app.core.config import get_settings

log = logging.getLogger(__name__)

PREFIX = "legacylift:analytics:"


class Backend(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str, ttl: int) -> None: ...
    def clear(self) -> None: ...


class NullCache:
    def get(self, key: str) -> str | None:
        return None

    def set(self, key: str, value: str, ttl: int) -> None:
        return None

    def clear(self) -> None:
        return None


class MemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            hit = self._data.get(key)
            if hit is None or hit[0] <= time.monotonic():
                self._data.pop(key, None)
                return None
            return hit[1]

    def set(self, key: str, value: str, ttl: int) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + ttl, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class RedisCache:
    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(url, socket_timeout=0.5, socket_connect_timeout=0.5, decode_responses=True)

    def get(self, key: str) -> str | None:
        return self._client.get(PREFIX + key)  # type: ignore[return-value]

    def set(self, key: str, value: str, ttl: int) -> None:
        self._client.set(PREFIX + key, value, ex=ttl)

    def clear(self) -> None:
        for key in self._client.scan_iter(PREFIX + "*"):
            self._client.delete(key)


_backend: Backend | None = None
_backend_name: str | None = None
_lock = threading.Lock()


def get_backend() -> Backend:
    global _backend, _backend_name
    settings = get_settings()
    with _lock:
        if _backend is None or _backend_name != settings.cache_backend:
            if settings.cache_backend == "redis":
                _backend = RedisCache(settings.redis_url)
            elif settings.cache_backend == "memory":
                _backend = MemoryCache()
            else:
                _backend = NullCache()
            _backend_name = settings.cache_backend
        return _backend


def reset() -> None:
    """Forget the backend and what it holds (tests)."""
    global _backend, _backend_name
    with _lock:
        if _backend is not None:
            try:
                _backend.clear()
            except Exception:  # pragma: no cover - Redis may be absent
                pass
        _backend, _backend_name = None, None


class Stats:
    hits = 0
    misses = 0
    errors = 0


def key_of(name: str, params: dict[str, Any]) -> str:
    raw = json.dumps(params, sort_keys=True, default=str)
    return f"{name}:{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def cached(name: str, params: dict[str, Any], compute: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Return the cached answer for (name, params) or compute and cache it for `analytics_cache_seconds`."""
    ttl = get_settings().analytics_cache_seconds
    key = key_of(name, params)
    backend = get_backend()
    try:
        hit = backend.get(key)
        if hit is not None:
            Stats.hits += 1
            return json.loads(hit)  # type: ignore[no-any-return]
    except Exception as exc:
        Stats.errors += 1
        log.warning("analytics cache read failed: %s", type(exc).__name__)
    Stats.misses += 1
    value = compute()
    try:
        backend.set(key, json.dumps(value, default=str), ttl)
    except Exception as exc:
        Stats.errors += 1
        log.warning("analytics cache write failed: %s", type(exc).__name__)
    return value
