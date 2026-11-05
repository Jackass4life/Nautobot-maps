"""Domain errors and the handlers that turn them into HTTP responses."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorDto(BaseModel):
    detail: str


class NotFoundError(Exception):
    def __init__(self, resource: str, resource_id: object) -> None:
        super().__init__(f"{resource} {resource_id} not found")


class ConflictError(Exception):
    pass


class UnauthorizedError(Exception):
    """Not signed in, and the action needs a role."""


class ForbiddenError(Exception):
    """Signed in (or not) without a high enough role, or a refused request."""


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    401: {"model": ErrorDto, "description": "Not signed in"},
    403: {"model": ErrorDto, "description": "Role too low, or a cross-site change"},
    404: {"model": ErrorDto, "description": "Not found"},
    409: {"model": ErrorDto, "description": "Conflict"},
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content=ErrorDto(detail=str(exc)).model_dump())

    @app.exception_handler(ConflictError)
    async def conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content=ErrorDto(detail=str(exc)).model_dump())

    @app.exception_handler(UnauthorizedError)
    async def unauthorized(_: Request, exc: UnauthorizedError) -> JSONResponse:
        return JSONResponse(status_code=401, content=ErrorDto(detail=str(exc)).model_dump())

    @app.exception_handler(ForbiddenError)
    async def forbidden(_: Request, exc: ForbiddenError) -> JSONResponse:
        return JSONResponse(status_code=403, content=ErrorDto(detail=str(exc)).model_dump())
