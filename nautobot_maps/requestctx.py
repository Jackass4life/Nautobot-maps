"""The request being answered, for code below the routes (#436): ``auth`` and
``mcp`` read ``requestctx.request`` (``headers``, ``remote_addr``, ``method``,
``path``, ``host``) and keep things for that one request in ``requestctx.g``.

A FastAPI route binds its request with ``bind()`` (an async dependency, so
the value lives in the request's own context and reaches the route's thread).
"""

import contextvars
import types

_current: contextvars.ContextVar = contextvars.ContextVar("nautobot_maps_request", default=None)


class StarletteRequestView:
    """A Starlette request with the names ``auth`` and ``mcp`` read."""

    def __init__(self, request):
        self._request = request
        self.headers = request.headers  # case-insensitive, .get(name, default)
        self.method = request.method
        self.path = request.url.path
        self.host = request.headers.get("host", "")
        self.remote_addr = request.client.host if request.client else ""
        self.g = types.SimpleNamespace()

    @property
    def args(self):
        return self._request.query_params


def bind(request) -> None:
    """Make *request* (Starlette's) the current one for this request's context."""
    _current.set(StarletteRequestView(request))


def current():
    """The request being answered (bound by every route, ``api.deps``)."""
    view = _current.get()
    if view is None:
        raise RuntimeError("No request is being answered here")
    return view


def store():
    """Somewhere to keep things for the request being answered."""
    return current().g


def bind_view(view):
    """Make *view* (anything with the request names above) the current request;
    returns a token for ``reset``.  For tests and in-process callers."""
    return _current.set(view)


def reset(token) -> None:
    _current.reset(token)


class _Proxy:
    def __init__(self, resolve):
        object.__setattr__(self, "_resolve", resolve)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_resolve")(), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_resolve")(), name, value)


request = _Proxy(current)
g = _Proxy(store)
