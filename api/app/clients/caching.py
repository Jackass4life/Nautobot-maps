"""The response cache, shared by the app and the API clients (#165, #596).

cachelib: ``CACHE_TYPE=SimpleCache`` (in-process; development, one worker) by
default, ``RedisCache`` with ``CACHE_REDIS_URL`` to share it across workers, or
``NullCache`` (nothing kept). The current app's nautobot_maps/caching.py.
"""

import redis
from cachelib import BaseCache, NullCache, RedisCache, SimpleCache

from app.utils.config import get_settings

# The current app's (Flask-Caching's) prefix, kept: during the switchover the old
# and the new app share the same keys (the board, the geocoder's rate limit).
KEY_PREFIX = "flask_cache_"


class _Cache:
    """The configured backend; ``init()`` (at startup) sets it. Until then an
    in-process one, so a module or test that uses the cache early works."""

    def __init__(self) -> None:
        self.backend: BaseCache = SimpleCache(default_timeout=get_settings().cache_ttl)

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self.backend, name)


cache = _Cache()


def backend_for(cache_type: str, redis_url: str, timeout: int) -> BaseCache:
    kind = (cache_type or "SimpleCache").rsplit(".", 1)[-1].lower().removesuffix("cache")
    if kind == "redis":
        if not redis_url:
            raise ValueError("CACHE_TYPE=RedisCache needs CACHE_REDIS_URL")
        client = redis.from_url(redis_url)
        return RedisCache(host=client, default_timeout=timeout, key_prefix=KEY_PREFIX)
    if kind == "simple":
        return SimpleCache(default_timeout=timeout)
    if kind == "null":
        return NullCache(default_timeout=timeout)
    raise ValueError(f"CACHE_TYPE must be SimpleCache, RedisCache or NullCache, not {cache_type!r}")


def init() -> None:
    """The cache from the settings. Works outside requests too (the scheduler uses it)."""
    settings = get_settings()
    cache.backend = backend_for(settings.cache_type, settings.cache_redis_url, settings.cache_ttl)


def get(key: str):  # type: ignore[no-untyped-def]
    return cache.get(key)


def set(key: str, data: object, timeout: int | None = None) -> None:
    cache.set(key, data, timeout=timeout)


def invalidate_alert_board() -> None:
    cache.delete("alert-board-data:v3")
    cache.delete("alert-board-data:v3:include-non-operational")
    # The map's marker colours (#234).
    cache.delete("location-alert-levels:v1")
