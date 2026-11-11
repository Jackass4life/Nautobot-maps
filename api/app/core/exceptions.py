"""Errors and the handlers that turn them into HTTP responses.

Every refusal answers ``{"error": "<one plain sentence>"}``, as the current app
does (#592), with ``detail`` (how to allow it), ``required_role`` and
``current_role`` where they apply.
"""

from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from app.core.settings import get_settings
from app.frontend import security


class Error(BaseModel):
    """Every refusal and error: one plain sentence, and for a role refusal the role it
    needs. The one error model of the whole API, the current app's (#604)."""

    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str | None = Field(None, description="How to allow it, when the setup refuses it.")
    required_role: Literal["viewer", "operator", "admin"] | None = None
    current_role: str | None = None
    missing_device_ids: list[str] | None = Field(
        None, description="Devices with no open alert: nothing was changed."
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


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": ErrorDto, "description": "Not a valid request"},
    401: {"model": ErrorDto, "description": "Not signed in, or a rejected API token"},
    403: {"model": ErrorDto, "description": "Role too low, or a cross-site change"},
    404: {"model": ErrorDto, "description": "Not found"},
    409: {"model": ErrorDto, "description": "Conflict"},
}


def responses(*statuses: int) -> dict[int | str, dict[str, object]]:
    return {status: ERROR_RESPONSES[status] for status in statuses}


# The current app's wording for these (#604).
_HTTP_MESSAGES = {404: "Not found", 405: "Method not allowed", 500: "Internal server error"}


def install_error_handlers(app: FastAPI) -> None:
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        from app.v1 import deps

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
            refused = auth_service.check_request(request, user, get_settings())
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
            headers=dict(security.security_headers(get_settings())),
        )
