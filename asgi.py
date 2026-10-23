"""The app as ASGI (#436): FastAPI on the outside, the Flask app mounted
behind it until every area has moved (the moved ones: nautobot_maps/api/).
gunicorn serves ``asgi:app`` with
uvicorn workers (gunicorn_config.py); ``python app.py`` serves it for
development.  Routes FastAPI doesn't have yet fall through to Flask, which
keeps its own sign-in check, headers and error pages for them.
"""

from a2wsgi import WSGIMiddleware
from fastapi import FastAPI
from starlette.routing import Match, Mount

import app as flask_app
import nautobot_maps.api
from nautobot_maps import access_log


def _input_ends_with_the_body(wsgi_app):
    """a2wsgi's input ends where the request body does, also for a chunked
    upload (no Content-Length).  Saying so lets Flask read such a body, as it
    did under gunicorn; otherwise it would read nothing."""

    def wrapped(environ, start_response):
        environ.setdefault("wsgi.input_terminated", True)
        return wsgi_app(environ, start_response)

    return wrapped


# The API docs come at the end of the move (#436): /docs is still Flask's explorer.
api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
# The areas on FastAPI so far, in front of Flask.
nautobot_maps.api.install(api)


class FlaskFallback(Mount):
    """The Flask app, for every path FastAPI doesn't serve.  A path it does
    serve, asked with another method, is FastAPI's 405 (as Flask answered),
    not a fall-through to Flask's 404."""

    def matches(self, scope):
        if scope["type"] == "http":
            for router in nautobot_maps.api.ROUTERS:
                for route in router.routes:
                    if route.matches(scope)[0] != Match.NONE:
                        return Match.NONE, {}
        return super().matches(scope)


# Everything FastAPI doesn't serve itself: up to 16 requests at once per worker.
api.router.routes.append(FlaskFallback("/", app=WSGIMiddleware(_input_ends_with_the_body(flask_app.app), workers=16)))

app = access_log.AccessLog(api)
