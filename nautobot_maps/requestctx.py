"""The current request, for code that runs under Flask or FastAPI (#436).

While the routes move from Flask to FastAPI, ``auth`` (and later other
modules) must work for both.  ``requestctx.request`` is the request being
answered: FastAPI's, wrapped to look like Flask's for what that code reads
(``headers``, ``remote_addr``, ``method``, ``path``, ``host``), or Flask's own.
``requestctx.g`` is somewhere to keep things for that one request.

A FastAPI route binds its request with ``bind()`` (an async dependency, so
the value lives in the request's own context and reaches the route's thread).
"""

import contextvars
import types

_current: contextvars.ContextVar = contextvars.ContextVar("nautobot_maps_request", default=None)


class StarletteRequestView:
    """A Starlette request with the Flask names ``auth`` reads."""

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
    """The current request: FastAPI's bound one, else Flask's."""
    view = _current.get()
    if view is not None:
        return view
    import flask

    return flask.request


def store():
    """Per-request storage: the bound request's, else Flask's ``g``."""
    view = _current.get()
    if view is not None:
        return view.g
    import flask

    return flask.g


class _Proxy:
    def __init__(self, resolve):
        object.__setattr__(self, "_resolve", resolve)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, "_resolve")(), name)

    def __setattr__(self, name, value):
        setattr(object.__getattribute__(self, "_resolve")(), name, value)


request = _Proxy(current)
g = _Proxy(store)
