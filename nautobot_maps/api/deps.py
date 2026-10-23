"""What every route shares (#436), so it answers exactly as the Flask
routes did: the sign-in check and roles (``auth``, the same code), the
security headers and gzip (``compression``'s rules), JSON in Flask's format,
and errors as ``{"error": ...}``.
"""

import dataclasses
import datetime
import decimal
import email.utils
import gzip
import inspect
import json
import logging
import re
import uuid

from fastapi import Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.dependencies.utils import get_flat_params
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, StreamingResponse

from nautobot_maps import auth, compression, requestctx, settings, web
from nautobot_maps.api.answers import common

logger = logging.getLogger(__name__)


def _json_default(value):
    """Values JSON has no type for, as Flask wrote them: a date or datetime as
    an HTTP date in GMT (a naive one taken as UTC), a decimal or UUID as text,
    a dataclass as an object."""
    if isinstance(value, datetime.date):
        if not isinstance(value, datetime.datetime):
            value = datetime.datetime.combine(value, datetime.time(), tzinfo=datetime.UTC)
        elif value.tzinfo is None:
            value = value.replace(tzinfo=datetime.UTC)
        else:
            value = value.astimezone(datetime.UTC)
        return email.utils.format_datetime(value, usegmt=True)
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if hasattr(value, "__html__"):
        return str(value.__html__())
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class Json(JSONResponse):
    """JSON as Flask writes it: keys sorted, non-ASCII escaped, compact, a
    newline at the end, dates and decimals as Flask turns them into JSON."""

    def render(self, content) -> bytes:
        text = json.dumps(content, default=_json_default, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
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


async def json_body(request: Request):
    """The request's JSON as Flask's ``get_json(silent=True)`` read it: None
    unless it says it is JSON (``application/json`` or ``+json``) and parses.
    The routes check its shape themselves, with their own messages."""
    mimetype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if not (mimetype == "application/json" or (mimetype.startswith("application/") and mimetype.endswith("+json"))):
        return None
    try:
        return json.loads(await request.body())
    except ValueError:
        return None


def limited_body(limit: int):
    """A dependency: at most *limit* + 1 bytes of the body, read as they come,
    so an oversized (also a chunked) request is never held whole."""

    async def read(request: Request) -> bytes:
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > limit:
                return bytes(body[: limit + 1])
        return bytes(body)

    return read


async def form_values(request: Request) -> dict:
    """The request's form (URL-encoded or multipart) as Flask's ``request.form``
    read it; empty for any other body."""
    mimetype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if mimetype not in ("application/x-www-form-urlencoded", "multipart/form-data"):
        return {}
    try:
        form = await request.form()
    except Exception:
        return {}
    return {key: value for key, value in form.items() if isinstance(value, str)}


def body_schema(model) -> dict:
    """``openapi_extra`` describing a route's JSON body with *model*: the API
    spec shows it, while the route reads the body with ``json_body``."""
    return {
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": model.model_json_schema(by_alias=True)}},
        }
    }


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
    if not settings.COMPRESS_RESPONSES or not compression.compressible(mimetype):
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


# " (#180)" / " (#227, #228)": issue references, for developers, not readers.
_ISSUE_REFS = re.compile(r"\s*\(#\d+(?:,\s*#\d+)*\)")


# The tests set this to check each answer against the model the API spec
# shows for it (#449): called with the route and its answer, before gzip.
ANSWER_CHECK = None


def _error_response(description: str) -> dict:
    return {"model": common.Error, "description": description}


class AppRoute(APIRoute):
    """Every route: ``finish`` on its answer, and in the API spec its
    docstring's first paragraph as the summary, the rest as the description,
    issue numbers left out, and the errors it answers as the app sends them:
    400 for the API's bad input (``openapi`` drops FastAPI's 422), 401/403 when it needs
    a role.  (HEAD: ``HeadAsGet``.)"""

    def __init__(self, path, endpoint, **kwargs):
        doc = inspect.getdoc(endpoint) or ""
        summary, _, description = doc.partition("\n\n")
        # The router passes both, as None when the route doesn't set them.
        if not kwargs.get("summary"):
            kwargs["summary"] = _ISSUE_REFS.sub("", " ".join(summary.split())) or None
        if not kwargs.get("description"):
            kwargs["description"] = _ISSUE_REFS.sub("", description.strip())
        super().__init__(path, endpoint, **kwargs)
        # A page answers bad input with an error page, the API with JSON.
        takes_input = get_flat_params(self.dependant) or self.body_field or "requestBody" in (self.openapi_extra or {})
        if takes_input and path.startswith("/api/"):
            self.responses.setdefault(400, _error_response("Bad input"))
        if required_role(self):
            self.responses.setdefault(401, _error_response("Not signed in"))
            self.responses.setdefault(403, _error_response("The role is too low"))

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def finished(request: Request):
            response = await handler(request)
            if ANSWER_CHECK is not None:
                ANSWER_CHECK(self, response)
            return finish(request, response)

        return finished


def _error_json(request: Request, body: dict, status: int):
    return finish(request, Json(body, status_code=status))


# Flask's wording for these (web.api_http_error).
_HTTP_MESSAGES = {404: "Not found", 405: "Method not allowed", 500: "Internal server error"}
# The page a browser gets for these (templates/error.html), as Flask's.
ERROR_PAGES = {
    404: ("Page Not Found", "The page you are looking for does not exist. Check the URL or head back to the map."),
    405: ("Method Not Allowed", "The HTTP method used is not allowed for this URL."),
    500: ("Internal Server Error", "Something went wrong on our end. Please try again later."),
}


def _best_match(accept: str, offers: tuple[str, ...]) -> str | None:
    """The offer the Accept header prefers, as werkzeug's ``best_match``: each
    offer's quality from its most specific match, the first on a tie; None
    when the header is empty or accepts none of them."""
    entries = []
    for part in accept.split(","):
        media, _, params = part.strip().partition(";")
        media = media.strip().lower()
        if not media:
            continue
        quality = 1.0
        for param in params.split(";"):
            name, _, value = param.strip().partition("=")
            if name.strip() == "q":
                try:
                    quality = float(value)
                except ValueError:
                    quality = 0.0
        entries.append((media, quality))
    best, best_quality = None, 0.0
    for offer in offers:
        kind = offer.split("/")[0]
        matches = [(3, q) for m, q in entries if m == offer]
        matches += [(2, q) for m, q in entries if m == f"{kind}/*"]
        matches += [(1, q) for m, q in entries if m == "*/*"]
        if not matches:
            continue
        quality = max(matches)[1]
        if quality > best_quality:
            best, best_quality = offer, quality
    return best


def wants_json(request: Request) -> bool:
    """JSON for the API and for clients that prefer it; a page for browsers."""
    if request.url.path.startswith("/api/"):
        return True
    return _best_match(request.headers.get("accept", ""), ("application/json", "text/html")) == "application/json"


class HeadAsGet:
    """HEAD as Flask answered it: the GET route's answer without its body
    (headers and length included).  ASGI middleware around the app, so the
    API spec lists each route once."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def without_body(message):
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, without_body)


def _error_page(request: Request, status: int):
    from fastapi.responses import HTMLResponse

    from nautobot_maps import templating

    title, message = ERROR_PAGES[status]
    # No top bar on it: no signed-in user to look up (the request may have
    # matched no route, so none was bound).
    html = templating.render("error.html", error_code=status, error_title=title, error_message=message, bar_user=None)
    return finish(request, HTMLResponse(html, status_code=status))


def openapi(app) -> dict:
    """The API spec, without FastAPI's 422 for bad input: the app answers 400
    with ``{"error": ...}`` (``invalid_request``), which ``AppRoute`` lists."""
    if app.openapi_schema is None:
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(title=app.title, version=app.version, description=app.description, routes=app.routes)
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                operation.get("responses", {}).pop("422", None)
        for name in ("HTTPValidationError", "ValidationError"):
            schema.get("components", {}).get("schemas", {}).pop(name, None)
        app.openapi_schema = schema
    return app.openapi_schema


def install_error_handlers(app) -> None:
    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        response = Json(exc.body, status_code=exc.status)
        route = request.scope.get("route")
        if ANSWER_CHECK is not None and isinstance(route, AppRoute):
            ANSWER_CHECK(route, response)
        return finish(request, response)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        if exc.status_code in (404, 405):
            # As Flask, whose sign-in check ran before routing: someone not
            # allowed in gets 401/403, not a 404 that tells which paths exist.
            requestctx.bind(request)
            refused = await run_in_threadpool(auth.check_viewer)
            if refused is not None:
                return _error_json(request, refused[0], refused[1])
        if exc.status_code in ERROR_PAGES and not wants_json(request):
            response = _error_page(request, exc.status_code)
        else:
            message = _HTTP_MESSAGES.get(exc.status_code) or exc.detail or "Request failed"
            response = _error_json(request, {"error": message}, exc.status_code)
        # E.g. a 405's Allow: the methods the path does take, as Flask sent;
        # HEAD with GET (HeadAsGet).
        for name, value in (exc.headers or {}).items():
            if name.lower() == "allow" and "GET" in value.split(", ") and "HEAD" not in value:
                value = f"{value}, HEAD"
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
        if not wants_json(request):
            return _error_page(request, 500)
        return _error_json(request, {"error": "Internal server error"}, 500)
