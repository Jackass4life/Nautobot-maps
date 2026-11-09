"""The API's spec and docs as the app's own routes (#592 review), behind the same
sign-in rules as every other route (``check_request``), as the current app serves
/api/openapi.json and /api/docs (#483): with AUTH_REQUIRE_VIEWER they need the
viewer role, and a rejected or status-only token is refused."""

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.frontend import controller as frontend
from app.utils.auth import check_request
from app.v1 import sections

OPENAPI_URL = "/api/openapi.json"
DOCS_URL = "/api/docs"

router = APIRouter(
    dependencies=[Depends(check_request)],
    include_in_schema=False,
    tags=sections.tag("documentation"),
)


@router.get(OPENAPI_URL)
def openapi(request: Request) -> JSONResponse:
    app: FastAPI = request.app
    return JSONResponse(app.openapi())


@router.get(DOCS_URL)
def docs() -> Response:
    """The API page: web's page with Swagger UI bundled (the current app's, #483), under
    the strict CSP. FastAPI's own loads scripts from a CDN, which the CSP blocks."""
    return frontend.shell()
