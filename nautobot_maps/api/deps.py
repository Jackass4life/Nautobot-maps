"""What every FastAPI route shares (#436), so it answers exactly as the Flask
routes did: the sign-in check and roles (``auth``, the same code), the
security headers and gzip (``compression``'s rules), JSON in Flask's format,
and errors as ``{"error": ...}``.
"""

import gzip
import json
import logging

from fastapi import Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from flask.json.provider import _default as flask_json_default
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, StreamingResponse

from nautobot_maps import auth, compression, requestctx, settings, web

logger = logging.getLogger(__name__)


class Json(JSONResponse):
    """JSON as Flask writes it: keys sorted, non-ASCII escaped, compact, a
    newline at the end, dates and decimals as Flask turns them into JSON."""

    def render(self, content) -> bytes:
        text = json.dumps(content, default=flask_json_default, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return (text + "\n").encode("utf-8")


class ApiError(Exception):
    """A refusal or error with its JSON body, e.g. auth's ``(body, status)``."""

    def __init__(self, body: dict, status: int):
        super().__init__(body.get("error", ""))
        self.body = body
        self.status = status


async def bind_request(request: Request) -> None:
    # async: the request is bound in its own context, which the route's thread copies.
    requestctx.bind(request)


def check_viewer() -> None:
    """The sign-in check before every route (Flask: ``before_request``)."""
    refused = auth.check_viewer()
    if refused is not None:
        raise ApiError(*refused)


def require_role(role: str, open_when_disabled: bool = False):
    """A route dependency: *role* or a refusal, as ``auth.require_role``."""

    def dependency() -> None:
        refused = auth.role_problem(role, open_when_disabled)
        if refused is not None:
            raise ApiError(*refused)

    # For the API explorer and the route table, like Flask's views.
    dependency.required_role = auth.normalize_role(role)
    return Depends(dependency)


ROUTER_DEPENDENCIES = [Depends(bind_request), Depends(check_viewer)]


def required_role(route) -> str | None:
    for dependency in route.dependant.dependencies:
        role = getattr(dependency.call, "required_role", None)
        if role:
            return role
    return None


def _accepts_gzip(request: Request) -> bool:
    for part in request.headers.get("accept-encoding", "").split(","):
        name, _, params = part.strip().partition(";")
        if name.strip().lower() in ("gzip", "*"):
            quality = params.strip().removeprefix("q=").strip()
            try:
                return float(quality) > 0 if quality else True
            except ValueError:
                return True
    return False


def finish(request: Request, response):
    """The security headers (#198) and gzip (#349) on a route's answer: the
    same headers and rules as Flask's ``after_request`` hooks."""
    headers = response.headers
    for name, value in (
        ("content-security-policy", web.content_security_policy()),
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
    ):
        if name not in headers:
            headers[name] = value
    mimetype = (headers.get("content-type") or "").split(";")[0].strip().lower()
    if not settings.COMPRESS_RESPONSES or not compression._compressible(mimetype):
        return response
    vary = [part.strip() for part in headers.get("vary", "").split(",") if part.strip()]
    if "accept-encoding" not in (part.lower() for part in vary):
        headers["vary"] = ", ".join([*vary, "Accept-Encoding"])
    if (
        response.status_code != 200
        or request.method != "GET"
        or "content-encoding" in headers
        or "no-transform" in (headers.get("cache-control") or "")
        or not _accepts_gzip(request)
        # Streamed (the exports) and files go as they are.
        or isinstance(response, (StreamingResponse, FileResponse))
        or len(response.body) < compression.MIN_BYTES
    ):
        return response
    response.body = gzip.compress(response.body, compresslevel=compression.LEVEL, mtime=0)
    headers["content-encoding"] = "gzip"
    headers["content-length"] = str(len(response.body))
    if "accept-ranges" in headers:
        del headers["accept-ranges"]
    return response


class AppRoute(APIRoute):
    """Every route: HEAD with GET (as Flask), and ``finish`` on its answer."""

    def __init__(self, path, endpoint, **kwargs):
        methods = set(kwargs.get("methods") or ["GET"])
        if "GET" in methods:
            methods.add("HEAD")
        kwargs["methods"] = methods
        super().__init__(path, endpoint, **kwargs)

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def finished(request: Request):
            return finish(request, await handler(request))

        return finished


def _error_json(request: Request, body: dict, status: int):
    return finish(request, Json(body, status_code=status))


# Flask's wording for these (web.api_http_error).
_HTTP_MESSAGES = {404: "Not found", 405: "Method not allowed", 500: "Internal server error"}


def install_error_handlers(app) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return _error_json(request, exc.body, exc.status)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        message = _HTTP_MESSAGES.get(exc.status_code) or exc.detail or "Request failed"
        response = _error_json(request, {"error": message}, exc.status_code)
        # E.g. a 405's Allow: the methods the path does take, as Flask sent.
        for name, value in (exc.headers or {}).items():
            response.headers[name] = value
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # 400 with one plain sentence, as the Flask routes answered bad input.
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ())[1:]) or "request"
        return _error_json(request, {"error": f"{where}: {first.get('msg', 'invalid')}"}, 400)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        logger.error("Unhandled application error: %s", exc)
        return _error_json(request, {"error": "Internal server error"}, 500)
