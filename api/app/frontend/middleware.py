"""Every answer's security headers, and gzip for large text (#594): what the
current app's deps.finish does to each route's answer, as ASGI middleware so
errors and 404s get the headers too."""

import gzip

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.settings import get_settings
from app.frontend import compression, security


class FinishMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        request_headers = Headers(scope=scope)
        may_gzip = (
            settings.compress_responses
            and scope["method"] == "GET"
            and compression.accepts_gzip(request_headers.get("accept-encoding", ""))
        )
        held: Message | None = None

        async def finish(message: Message) -> None:
            nonlocal held
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in security.security_headers(settings):
                    if name not in headers:
                        headers[name] = value
                mimetype = (headers.get("content-type") or "").split(";")[0].strip().lower()
                if not settings.compress_responses or not compression.compressible(mimetype):
                    await send(message)
                    return
                vary = [part.strip() for part in headers.get("vary", "").split(",") if part.strip()]
                if "accept-encoding" not in (part.lower() for part in vary):
                    headers["vary"] = ", ".join([*vary, "Accept-Encoding"])
                if (
                    not may_gzip
                    or message["status"] != 200
                    or "content-encoding" in headers
                    or "no-transform" in (headers.get("cache-control") or "")
                ):
                    await send(message)
                    return
                held = message  # until the body shows whether it is one piece
                return
            if held is None or message["type"] != "http.response.body":
                await send(message)
                return
            start, held = held, None
            body = message.get("body", b"")
            if message.get("more_body", False) or len(body) < compression.MIN_BYTES:
                # Streamed (more pieces to come) or small: as it is.
                await send(start)
                await send(message)
                return
            zipped = gzip.compress(body, compresslevel=compression.LEVEL, mtime=0)
            headers = MutableHeaders(scope=start)
            headers["content-encoding"] = "gzip"
            headers["content-length"] = str(len(zipped))
            if "accept-ranges" in headers:
                del headers["accept-ranges"]
            await send(start)
            await send({"type": "http.response.body", "body": zipped, "more_body": False})

        await self.app(scope, receive, finish)
