"""The access log (#193, #436): one line per request, the same under uvicorn as
it was under gunicorn's sync workers: client, signed-in user (the proxy's
header), request, status, size, time in ms, user agent.

ASGI middleware around the whole app, the mounted Flask app included, so
every request is logged once.  Successful health checks are dropped by
``logs.SkipSuccessfulHealthChecks``, which reads the same atoms (``U``, ``s``)
gunicorn's access log had.
"""

import logging
import time

from nautobot_maps import settings

logger = logging.getLogger("nautobot_maps.access")

FORMAT = '%(h)s "%(user)s" "%(r)s" %(s)s %(b)s %(M)sms "%(a)s"'


def _header(scope, name: str) -> str:
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers") or []:
        if key.lower() == wanted:
            return value.decode("latin-1")
    return ""


class AccessLog:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        response = {"status": 500, "size": 0}

        async def send_and_count(message):
            if message["type"] == "http.response.start":
                response["status"] = message["status"]
            elif message["type"] == "http.response.body":
                response["size"] += len(message.get("body") or b"")
            await send(message)

        try:
            await self.app(scope, receive, send_and_count)
        finally:
            query = scope.get("query_string") or b""
            target = scope.get("path", "") + (f"?{query.decode('latin-1')}" if query else "")
            client = scope.get("client") or ("-", 0)
            logger.info(
                FORMAT,
                {
                    "h": client[0],
                    "user": _header(scope, settings.AUTH_HEADER_USER) or "-",
                    "r": f"{scope.get('method', '-')} {target} HTTP/{scope.get('http_version', '1.1')}",
                    "s": response["status"],
                    "b": response["size"],
                    "M": round((time.perf_counter() - started) * 1000),
                    "a": _header(scope, "user-agent") or "-",
                    "U": scope.get("path", ""),
                },
            )
