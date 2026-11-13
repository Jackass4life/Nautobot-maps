"""What every route ported from the current app shares (#604): the current app's
nautobot_maps/api/deps.py on the new stack.

- ``Json``: answers as the current app writes them (keys sorted, compact, dates as
  Flask wrote them), so the pages and scripts read the same bytes.
- ``ROUTER_DEPENDENCIES``: the request bound for the code below (``requestctx``) and
  the sign-in check (#592's ``check_request``).
- ``require_permission``: the permission a route needs (#685, app/auth/permissions.py),
  kept on it for the spec and the route tests; ``PUBLIC`` for a route needing none.
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

from app.auth import dependencies as auth_dependencies
from app.auth import permissions
from app.auth.dependencies import check_request
from app.context import request as requestctx
from app.core.exceptions import ApiError
from app.core.exceptions import Error as ErrorDto
from app.utils.body import body_schema, json_body

__all__ = [
    "PUBLIC",
    "ROUTER_DEPENDENCIES",
    "ApiError",
    "AppRoute",
    "Json",
    "body_schema",
    "form_values",
    "json_body",
    "limited_body",
    "require_permission",
    "required_permissions",
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


def require_permission(*required: permissions.Permission, open_when_disabled: bool = False):  # type: ignore[no-untyped-def]
    """A route dependency: one of *required* (app/auth/permissions.py, #685), or a
    refusal (#592's rules). Several on a route: each is needed (a CSV: the page's
    permission and ``export.csv``)."""
    return Depends(
        auth_dependencies.require_permission(*required, open_when_disabled=open_when_disabled)
    )


# A route anyone reaches without signing in: health, metrics, the status page.
PUBLIC = Depends(auth_dependencies.public)


def required_permissions(route: APIRoute) -> list[tuple[str, ...]]:
    """Each permission dependency of *route*: the permissions any one of which it
    takes (``("public",)`` for a public route); every one of them is needed."""
    found = []
    for dependency in route.dependant.dependencies:
        names = getattr(dependency.call, "required_permissions", None)
        if names:
            found.append(tuple(names))
    return found


def required_role(route: APIRoute) -> str | None:
    """The lowest of the old roles (viewer, operator, admin) that may use *route*, for
    the API page and scripts; None when every signed-in role may (#592's notion)."""
    lowest = None
    for names in required_permissions(route):
        if names == (permissions.PUBLIC,):
            continue
        roles = [permissions.lowest_old_role(permissions.Permission(n)) for n in names]
        candidates = [r for r in roles if r]
        if not candidates:
            continue
        role = min(candidates, key=permissions.OLD_ROLES.index)
        if lowest is None or permissions.OLD_ROLES.index(role) > permissions.OLD_ROLES.index(
            lowest
        ):
            lowest = role
    return None if lowest == "viewer" else lowest


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

    from app.core.settings import get_settings
    from app.frontend import compression, security

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
    ``{"error": ...}``); an error's example is its description; the sections as
    its ordered tags (#623)."""
    if app.openapi_schema is None:
        from fastapi.openapi.utils import get_openapi

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            tags=app.openapi_tags,
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
    """The current app's handlers: app/core/exceptions.py installs them for the whole app."""
    from app.core.exceptions import install_error_handlers as install

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
