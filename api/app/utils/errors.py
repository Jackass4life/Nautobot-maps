"""Errors and the handlers that turn them into HTTP responses.

Every refusal answers ``{"error": "<one plain sentence>"}``, as the current app
does (#592), with ``detail`` (how to allow it), ``required_role`` and
``current_role`` where they apply.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.utils.config import Role


class ErrorDto(BaseModel):
    """A refusal or error: one plain sentence, and for a role refusal the role it needs."""

    model_config = ConfigDict(extra="forbid")

    error: str
    detail: str | None = Field(None, description="How to allow it, when the setup refuses it.")
    required_role: Role | None = None
    current_role: str | None = None


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


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.body, headers=exc.headers)
