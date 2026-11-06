"""What every route ported from the current app shares (#604): the current app's
nautobot_maps/api/deps.py on the new stack.

- ``Json``: answers as the current app writes them (keys sorted, compact, dates as
  Flask wrote them), so the pages and scripts read the same bytes.
- ``ROUTER_DEPENDENCIES``: the request bound for the code below (``requestctx``) and
  the sign-in check (#592's ``check_request``).
- ``require_role``: #592's, with the role kept on it for the spec and route tests.
- ``AppRoute``: a docstring's first paragraph as the summary, the rest as the
  description (issue numbers left out), a stable camelCase ``operation_id``, the
  errors a route answers, and the tests' answer check.
- ``json_body`` / ``body_schema`` / ``limited_body`` / ``form_values``: bodies as the
  current routes read them, checked by the routes themselves.

The security headers and gzip are app/frontend/middleware.py's, on every answer.
"""

import dataclasses
import datetime
import decimal
import email.utils
import inspect
import json
import re
import uuid

from fastapi import Depends, Request
from fastapi.dependencies.utils import get_flat_params
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.utils import requestctx
from app.utils.auth import check_request
from app.utils.auth import require_role as _require_role
from app.utils.body import body_schema, json_body
from app.utils.errors import ApiError
from app.utils.errors import Error as ErrorDto

__all__ = [
    "ROUTER_DEPENDENCIES",
    "ApiError",
    "AppRoute",
    "Json",
    "body_schema",
    "form_values",
    "json_body",
    "limited_body",
    "require_role",
]


def _json_default(value: object) -> object:
    """Values JSON has no type for, as Flask wrote them: a date or datetime as an HTTP
    date in GMT (a naive one taken as UTC), a decimal or UUID as text, a dataclass."""
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
    """JSON as the current app writes it: keys sorted, non-ASCII escaped, compact, a
    newline at the end, dates and decimals as Flask turned them into JSON."""

    def render(self, content: object) -> bytes:
        text = json.dumps(
            content,
            default=_json_default,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        return (text + "\n").encode("utf-8")


async def bind_request(request: Request) -> None:
    # async: the request is bound in its own context, which the route's thread copies.
    requestctx.bind(request)


ROUTER_DEPENDENCIES = [Depends(bind_request), Depends(check_request)]


def require_role(role: str, open_when_disabled: bool = False):  # type: ignore[no-untyped-def]
    """A route dependency: *role* or a refusal (#592's rules)."""
    dependency = _require_role(role, open_when_disabled)  # type: ignore[arg-type]
    # For the spec and the route tests, as the current app's.
    dependency.required_role = role  # type: ignore[attr-defined]
    return Depends(dependency)


def required_role(route: APIRoute) -> str | None:
    for dependency in route.dependant.dependencies:
        role = getattr(dependency.call, "required_role", None)
        if role:
            return role
    return None


def limited_body(limit: int):  # type: ignore[no-untyped-def]
    """A dependency: at most *limit* + 1 bytes of the body, read as they come, so an
    oversized (also a chunked) request is never held whole."""

    async def read(request: Request) -> bytes:
        body = bytearray()
        async for chunk in request.stream():
            body += chunk
            if len(body) > limit:
                return bytes(body[: limit + 1])
        return bytes(body)

    return read


async def form_values(request: Request) -> dict:
    """The request's form (URL-encoded or multipart) as Flask's ``request.form`` read
    it; empty for any other body."""
    mimetype = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if mimetype not in ("application/x-www-form-urlencoded", "multipart/form-data"):
        return {}
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - a body that isn't a form: no values, as Flask
        return {}
    return {key: value for key, value in form.items() if isinstance(value, str)}


# " (#180)" / " (#227, #228)": issue references, for developers, not readers.
_ISSUE_REFS = re.compile(r"\s*\(#\d+(?:,\s*#\d+)*\)")

# The tests set this to check each answer against the model the API spec shows for
# it (#449): called with the route and its answer.
ANSWER_CHECK = None


def _error_response(description: str) -> dict:
    return {"model": ErrorDto, "description": description}


def operation_id(path: str, methods: set[str] | None, endpoint) -> str:  # type: ignore[no-untyped-def]
    """A stable camelCase id: the HTTP verb and the function's name without ``api_``,
    ``Csv`` for a route's CSV twin (``/x.csv``): ``getLocations``, ``postAlertCases``."""
    verb = sorted(methods or {"GET"})[0].lower()
    name = endpoint.__name__.removeprefix("api_")
    words = [word for word in name.split("_") if word]
    camel = "".join(word[:1].upper() + word[1:] for word in words)
    return f"{verb}{camel}{'Csv' if path.endswith('.csv') else ''}"


class AppRoute(APIRoute):
    """Every ported route: summary and description from its docstring, a stable
    ``operation_id``, the errors it answers (400 for bad input, 401/403 with a role),
    and the tests' answer check."""

    def __init__(self, path: str, endpoint, **kwargs) -> None:  # type: ignore[no-untyped-def]
        doc = inspect.getdoc(endpoint) or ""
        summary, _, description = doc.partition("\n\n")
        if not kwargs.get("summary"):
            kwargs["summary"] = _ISSUE_REFS.sub("", " ".join(summary.split())) or None
        if not kwargs.get("description"):
            kwargs["description"] = _ISSUE_REFS.sub("", description.strip())
        if not kwargs.get("operation_id"):
            kwargs["operation_id"] = operation_id(path, kwargs.get("methods"), endpoint)
        super().__init__(path, endpoint, **kwargs)
        takes_input = (
            get_flat_params(self.dependant)
            or self.body_field
            or "requestBody" in (self.openapi_extra or {})
        )
        if takes_input and path.startswith("/api/"):
            self.responses.setdefault(400, _error_response("Bad input"))
        if required_role(self):
            self.responses.setdefault(401, _error_response("Not signed in"))
            self.responses.setdefault(403, _error_response("The role is too low"))

    def get_route_handler(self):  # type: ignore[no-untyped-def]
        handler = super().get_route_handler()

        async def checked(request: Request):  # type: ignore[no-untyped-def]
            response = await handler(request)
            if ANSWER_CHECK is not None:
                ANSWER_CHECK(self, response)
            return response

        return checked


def _accepts_gzip(request: Request) -> bool:
    from app.frontend import compression

    return compression.accepts_gzip(request.headers.get("accept-encoding", ""))


def finish(request: Request, response):  # type: ignore[no-untyped-def]
    """The security headers and gzip on one answer, as the current app's ``deps.finish``.
    Here app/frontend/middleware.py does this for every answer; this is its rules on a
    single response, for code and tests that call it directly."""
    import gzip

    from fastapi.responses import FileResponse, StreamingResponse

    from app.frontend import compression, security
    from app.utils.config import get_settings

    settings = get_settings()
    headers = response.headers
    for name, value in security.security_headers(settings):
        if name not in headers:
            headers[name] = value
    mimetype = (headers.get("content-type") or "").split(";")[0].strip().lower()
    if not settings.compress_responses or not compression.compressible(mimetype):
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


class HeadAsGet:
    """HEAD as the current app answers it: the GET route's answer without its body
    (headers and length included). ASGI middleware, so the spec lists each route once."""

    def __init__(self, app) -> None:  # type: ignore[no-untyped-def]
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return

        async def without_body(message) -> None:  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.body":
                message = {**message, "body": b""}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, without_body)


def openapi(app) -> dict:  # type: ignore[no-untyped-def]
    """The spec without FastAPI's 422 for bad input (the app answers 400 with
    ``{"error": ...}``); an error's example is its description."""
    if app.openapi_schema is None:
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes
        )
        error = {"$ref": "#/components/schemas/Error"}
        for operations in schema.get("paths", {}).values():
            for operation in operations.values():
                operation.get("responses", {}).pop("422", None)
                for response in operation.get("responses", {}).values():
                    content = response.get("content", {}).get("application/json", {})
                    if content.get("schema") == error:
                        content["example"] = {"error": response["description"]}
        for name in ("HTTPValidationError", "ValidationError"):
            schema.get("components", {}).get("schemas", {}).pop(name, None)
        app.openapi_schema = schema
    return app.openapi_schema


def install_error_handlers(app) -> None:  # type: ignore[no-untyped-def]
    """The current app's handlers: app/utils/errors.py installs them for the whole app."""
    from app.utils.errors import install_error_handlers as install

    install(app)


# Page or JSON by the Accept header, as the current app chose its error answers (#604).
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
    from app.services import navigation_service as navigation

    if request.url.path.startswith("/api/") and request.url.path not in navigation.PAGES_UNDER_API:
        return True
    return (
        _best_match(request.headers.get("accept", ""), ("application/json", "text/html"))
        == "application/json"
    )
