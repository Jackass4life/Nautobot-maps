"""What no section owns (#624): the old /docs and /openapi.json, which redirect to
the API page and its spec (#483), and /static/ (ported from
nautobot_maps/api/views.py, #604). Untagged and left out of the endpoint list."""

import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.frontend import files
from app.services import navigation_service as navigation
from app.v1 import deps

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)

STATIC = pathlib.Path(__file__).resolve().parent.parent.parent / "static"


# The old paths (#483): bookmarks and tools that read the spec keep working,
# with their query (a 301 is remembered, so it must not lose one).
def _moved(request: Request, path: str) -> RedirectResponse:
    query = request.url.query
    return RedirectResponse(f"{path}?{query}" if query else path, status_code=301)


@router.get("/docs", name="pages.api_docs_moved", include_in_schema=False)
def api_docs_moved(request: Request):
    return _moved(request, navigation.API_DOCS_PATH)


@router.get("/openapi.json", name="ops.openapi_moved", include_in_schema=False)
def openapi_moved(request: Request):
    return _moved(request, navigation.API_SPEC_PATH)


@router.get("/static/{path:path}", name="static", include_in_schema=False)
def static(request: Request, path: str):
    """The pages' scripts, styles, images and vendored libraries."""
    return files.send(request, STATIC, path)
