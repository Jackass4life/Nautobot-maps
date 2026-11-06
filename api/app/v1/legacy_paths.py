"""The current app's API paths (#613): ``/api/<path>`` answers 308 to
``/api/v1/<path>`` when that is a route, so a LibreNMS or Alertmanager webhook, a
script or a bookmark keeps working until it is changed. 308 keeps the method and
the body. Paths that are routes themselves (``/api/openapi.json``, ``/api/docs``,
``/api/healthz``) and anything that isn't a route under /api/v1 are left alone.
"""

from collections.abc import Callable, Sequence
from urllib.parse import quote

from starlette.routing import BaseRoute, Match
from starlette.types import ASGIApp, Receive, Scope, Send

PREFIX = "/api/v1"


def _is_route(routes: Sequence[BaseRoute], scope: Scope, path: str) -> bool:
    probe = {**scope, "path": path, "raw_path": path.encode(), "root_path": ""}
    # PARTIAL: the path is a route, another method; the moved route answers its 405.
    return any(route.matches(probe)[0] != Match.NONE for route in routes)


class LegacyApiRedirect:
    def __init__(self, app: ASGIApp, routes: Callable[[], Sequence[BaseRoute]]) -> None:
        self.app = app
        self.routes = routes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if path.startswith("/api/") and not path.startswith(PREFIX + "/"):
            routes = self.routes()
            moved = PREFIX + path[len("/api") :]
            if not _is_route(routes, scope, path) and _is_route(routes, scope, moved):
                query = scope.get("query_string") or b""
                location = quote(moved, safe="/%:@!$&'()*+,;=-._~")
                if query:
                    location += "?" + query.decode("latin-1")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 308,
                        "headers": [
                            (b"location", location.encode("latin-1")),
                            (b"content-length", b"0"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
        await self.app(scope, receive, send)
