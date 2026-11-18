"""Errors and the handlers that turn them into HTTP responses.

Every refusal answers ``{"error": "<one plain sentence>"}``, as the current app
does (#592), with ``detail`` (how to allow it), ``required_role`` and
``current_role`` where they apply.
"""

import html
from typing import Literal
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.core.settings import settings
from app.frontend import security


class Error(BaseModel):
    """Every refusal and error: one plain sentence, and for a role refusal the role it
    needs. The one error model of the whole API, the current app's (#604)."""

    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str | None = Field(None, description="How to allow it, when the setup refuses it.")
    required_role: Literal["viewer", "operator", "admin"] | None = None
    required_permission: str | None = Field(
        None, description="The permission it needs (#685): e.g. alerts:ack, admin."
    )
    current_role: str | None = None
    missing_device_ids: list[str] | None = Field(
        None, description="Devices with no open alert: nothing was changed."
    )
    sign_in: str | None = Field(
        None, description="Not signed in, with AD sign-in on (#717): the sign-in page, /auth."
    )


# The new stack's routes say ErrorDto: the same model.
ErrorDto = Error


class ApiError(Exception):
    """A refusal with its JSON body and status, e.g. auth's ``(body, status)``."""

    def __init__(self, body: dict, status: int, headers: dict[str, str] | None = None):
        super().__init__(body.get("error", ""))
        self.body = body
        self.status = status
        self.headers = headers


class NotFoundError(ApiError):
    def __init__(self, resource: str, resource_id: object) -> None:
        super().__init__({"error": f"{resource} {resource_id} not found"}, 404)


class ConflictError(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__({"error": message}, 409)


# The example's s_exceptions (#711), written here: refusals with a message.
class UnauthenticatedException(ApiError):
    """Not signed in, or a sign-in that didn't work: 401."""

    def __init__(self, message: str = "Authentication required") -> None:
        super().__init__({"error": message}, 401)


class UnauthorizedException(ApiError):
    """Signed in, without the scope: 403."""

    def __init__(self, message: str = "Insufficient permissions") -> None:
        super().__init__({"error": message}, 403)


class NotFoundException(ApiError):
    def __init__(self, message: str = "Not found") -> None:
        super().__init__({"error": message}, 404)


class ValidationErrorException(ApiError):
    def __init__(self, message: str) -> None:
        super().__init__({"error": message}, 400)


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorDto, "description": "Not a valid request"},
    401: {"model": ErrorDto, "description": "Not signed in, or a rejected API token"},
    403: {"model": ErrorDto, "description": "Role too low, or a cross-site change"},
    404: {"model": ErrorDto, "description": "Not found"},
    409: {"model": ErrorDto, "description": "Conflict"},
    429: {"model": ErrorDto, "description": "Too many tries: wait (Retry-After)"},
    503: {"model": ErrorDto, "description": "Not available now: try again shortly"},
}


def responses(*statuses: int) -> dict[int | str, dict[str, object]]:
    return {status: ERROR_RESPONSES[status] for status in statuses}


# The current app's wording for these (#604).
_HTTP_MESSAGES = {404: "Not found", 405: "Method not allowed", 500: "Internal server error"}


# Someone signed in without an identity, with AUTH_GROUP_ROLES=false (#715).
NO_ACCESS = "No access"


def no_access_page(exc: ApiError) -> HTMLResponse:
    """A browser gets a page, not JSON: what happened and how to sign out (the proxy
    would sign them straight back in as the same person otherwise). No styles or
    scripts: the CSP allows none inline."""
    sign_out = settings.AUTH_SIGN_OUT_URL
    link = f'<p><a href="{html.escape(sign_out)}">Sign out</a></p>' if sign_out else ""
    body = (
        '<!DOCTYPE html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{NO_ACCESS} · Nautobot Maps</title>"
        f"<h1>{NO_ACCESS}</h1><p>{html.escape(str(exc.body.get('detail', '')))}</p>{link}</html>"
    )
    return HTMLResponse(body, status_code=exc.status, headers={"Cache-Control": "no-store"})


def sign_in_address(request: Request, page: str) -> str:
    """/auth?redirect=<this page and its query>: only a path on this site comes back."""
    back = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return f"{page}?redirect={quote(back, safe='')}"


def install_error_handlers(app: FastAPI) -> None:
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> Response:
        from app.v1 import deps

        if exc.body.get("error") == NO_ACCESS and not deps.wants_json(request):
            return no_access_page(exc)
        # Not signed in, AD sign-in on (#717): a browser goes to sign in, and back after.
        if exc.status == 401 and exc.body.get("sign_in") and not deps.wants_json(request):
            return RedirectResponse(sign_in_address(request, exc.body["sign_in"]), status_code=303)
        response = JSONResponse(status_code=exc.status, content=exc.body, headers=exc.headers)
        # The tests check refusals of the ported routes against their models too (#449).
        route = request.scope.get("route")
        if deps.ANSWER_CHECK is not None and isinstance(route, APIRoute):
            deps.ANSWER_CHECK(route, response)
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        """As the current app: someone not allowed in gets 401/403 before a 404 tells which
        paths exist; the error as ``{"error": ...}``; a 405's Allow with HEAD beside GET."""
        if exc.status_code in (404, 405):
            from app.auth.dependencies import get_current_user
            from app.services import auth_service

            user = await get_current_user(request)
            refused = auth_service.check_request(request, user, settings)
            if refused is not None:
                return JSONResponse(status_code=refused[1], content=refused[0])
        message = _HTTP_MESSAGES.get(exc.status_code) or exc.detail or "Request failed"
        headers = {}
        for name, value in (exc.headers or {}).items():
            if name.lower() == "allow" and "GET" in value.split(", ") and "HEAD" not in value:
                value = f"{value}, HEAD"
            headers[name] = value
        return JSONResponse(
            status_code=exc.status_code, content={"error": message}, headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_: Request, exc: RequestValidationError) -> JSONResponse:
        """400 with one plain sentence, as the current app answers bad input."""
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(part) for part in first.get("loc", ())[1:]) or "request"
        return JSONResponse(
            status_code=400, content={"error": f"{where}: {first.get('msg', 'invalid')}"}
        )

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:
        """An unhandled error (#594 review): Starlette answers it outside the app's own
        middleware, so this answer carries the security headers itself."""
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"},
            headers=dict(security.security_headers(settings)),
        )
