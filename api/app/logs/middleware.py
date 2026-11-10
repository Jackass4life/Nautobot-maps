"""The access log (#193, #436), ported from nautobot_maps/access_log.py (#613):
one line per request, the same under uvicorn as
it was under gunicorn's sync workers: client, signed-in user (the proxy's
header), request, status, size, time in ms, user agent.

ASGI middleware around the whole app (outermost, app/api.py), so
every request is logged once, under the logger name ``app.access`` and the
part ``api`` or ``web`` (#633). The atoms (``h``, ``user``, ``r``, ``s``, ``b``, ``M``,
``a``, ``U``: gunicorn's access log's) are the line's extra fields; successful health
checks are dropped by ``logs.keep_access_line``.
"""

import time

from loguru import logger

from app.core.legacy_settings import settings
from app.logs import parts

FORMAT = '{h} "{user}" "{r}" {s} {b} {M}ms "{a}"'


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
            path = scope.get("path", "")
            query = scope.get("query_string") or b""
            target = path + (f"?{query.decode('latin-1')}" if query else "")
            client = scope.get("client") or ("-", 0)
            logger.bind(logger=parts.ACCESS_LOGGER, part=parts.of_request(path)).info(
                FORMAT,
                **{
                    "h": client[0],
                    "user": _header(scope, settings.AUTH_HEADER_USER) or "-",
                    "r": f"{scope.get('method', '-')} {target} "
                    f"HTTP/{scope.get('http_version', '1.1')}",
                    "s": response["status"],
                    "b": response["size"],
                    "M": round((time.perf_counter() - started) * 1000),
                    "a": _header(scope, "user-agent") or "-",
                    "U": path,
                },
            )
