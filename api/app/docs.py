"""The API's spec and docs as the app's own routes (#592 review), behind the same
sign-in rules as every other route (``check_request``), as the current app serves
/api/openapi.json and /api/docs (#483): with AUTH_REQUIRE_VIEWER they need the
viewer role, and a rejected or status-only token is refused."""

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from app.utils.auth import check_request

OPENAPI_URL = "/api/openapi.json"
DOCS_URL = "/api/docs"

router = APIRouter(dependencies=[Depends(check_request)], include_in_schema=False)


@router.get(OPENAPI_URL)
def openapi(request: Request) -> JSONResponse:
    app: FastAPI = request.app
    return JSONResponse(app.openapi())


@router.get(DOCS_URL)
def docs(request: Request) -> HTMLResponse:
    app: FastAPI = request.app
    return get_swagger_ui_html(openapi_url=OPENAPI_URL, title=f"{app.title} - API")
