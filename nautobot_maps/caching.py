"""The response cache, shared by the app and the API clients (#165).

cachelib, which Flask-Caching wrapped (#436): ``CACHE_TYPE=SimpleCache``
(in-process; development, one worker) by default, ``RedisCache`` with
``CACHE_REDIS_URL=redis://redis:6379/0`` in production to share it across the
gunicorn workers, or ``NullCache`` (nothing kept).
"""

import redis
from cachelib import BaseCache, NullCache, RedisCache, SimpleCache

from nautobot_maps import settings

# Flask-Caching's prefix, kept: during an upgrade the old and the new workers
# share the same keys (the board, the geocoder's rate limit).
KEY_PREFIX = "flask_cache_"


class _Cache:
    """The configured backend; ``init()`` (app.py) sets it.  Until then an
    in-process one, so a module or test that uses the cache early works."""

    def __init__(self):
        self.backend: BaseCache = SimpleCache(default_timeout=settings.CACHE_TTL)

    def __getattr__(self, name):
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
    """The cache from the settings.  Works outside requests too (the background scheduler uses it)."""
    cache.backend = backend_for(settings.CACHE_TYPE, settings.CACHE_REDIS_URL, settings.CACHE_TTL)


def get(key: str):
    return cache.get(key)


def set(key: str, data, timeout: int | None = None):  # noqa: A001 - mirrors cache.set
    cache.set(key, data, timeout=timeout)


def invalidate_alert_board() -> None:
    cache.delete("alert-board-data:v3")
    cache.delete("alert-board-data:v3:include-non-operational")
    # The map's marker colours (#234).
    cache.delete("location-alert-levels:v1")
