"""The response cache (Flask-Caching), shared by the app and the API clients (#165).

Defaults to SimpleCache (in-process) for development / single-worker setups.
Set CACHE_TYPE=RedisCache and CACHE_REDIS_URL=redis://redis:6379/0 in
production to share the cache across Gunicorn workers.
"""

from flask_caching import Cache

from nautobot_maps import settings

cache = Cache()


def init_app(app) -> None:
    app.config["CACHE_TYPE"] = settings.CACHE_TYPE
    app.config["CACHE_DEFAULT_TIMEOUT"] = settings.CACHE_TTL
    if settings.CACHE_REDIS_URL:
        app.config["CACHE_REDIS_URL"] = settings.CACHE_REDIS_URL
    # Works outside requests too (the background scheduler uses it).
    cache.init_app(app)


def get(key: str):
    return cache.get(key)


def set(key: str, data, timeout: int | None = None):  # noqa: A001 - mirrors cache.set
    cache.set(key, data, timeout=timeout)


def invalidate_alert_board() -> None:
    cache.delete("alert-board-data:v3")
    cache.delete("alert-board-data:v3:include-non-operational")
