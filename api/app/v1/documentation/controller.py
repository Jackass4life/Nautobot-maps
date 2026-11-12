"""The documentation (#433, #463; #624): the guides as pages and as JSON, their
images, the endpoint list, and the API page with its spec.

Two routers: ``router`` as the other sections', and ``spec_router`` for
/api/docs and /api/openapi.json (#592 review), behind the same sign-in rules
(``check_request``), as the current app serves them (#483): with
AUTH_REQUIRE_VIEWER they need the viewer role, and a rejected or status-only
token is refused."""

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from app.auth.dependencies import check_request
from app.frontend import controller as frontend
from app.frontend import files
from app.services import apidocs_service as apidocs
from app.services import guide_service as guide
from app.v1 import deps, sections
from app.v1.common import dtos as common
from app.v1.documentation import dtos

OPENAPI_URL = "/api/openapi.json"
DOCS_URL = "/api/docs"

router = APIRouter(
    route_class=deps.AppRoute,
    dependencies=deps.ROUTER_DEPENDENCIES,
    tags=sections.tag("documentation"),
)
spec_router = APIRouter(
    dependencies=[Depends(check_request)],
    include_in_schema=False,
    tags=sections.tag("documentation"),
)


@router.get("/help", name="help.help_page", include_in_schema=False)
@router.get(
    "/help/{slug}",
    name="help.help_page",
    include_in_schema=False,
)
def help_page(slug: str = "overview"):
    """The documentation: the overview, then each guide.

    Built with Nuxt (#463); the page reads ``/api/help/<slug>``.  An unknown
    guide is a 404 here, not a page that says so.
    """
    if guide.page(slug) is None:
        raise HTTPException(status_code=404)
    return frontend.shell()


@router.get(
    "/api/v1/help/{slug}",
    name="help.api_help",
    response_model=dtos.HelpPage,
    responses={404: {"model": common.Error, "description": "No such guide"}},
)
def api_help(slug: str):
    """One documentation page (#433): its title and HTML, and the list of guides."""
    found = guide.page(slug)
    if found is None:
        if not guide.contents():
            # An API image built without the docs context (#672).
            return deps.Json({"error": guide.NOT_INCLUDED}, status_code=404)
        return deps.Json({"error": "No such guide"}, status_code=404)
    return deps.Json({**found, "contents": guide.contents()})


@router.get(
    "/help/images/{name:path}",
    name="help.help_image",
    include_in_schema=False,
)
def help_image(request: Request, name: str):
    """The screenshots the documentation shows."""
    return files.send(request, guide.IMAGES, name, max_age=3600)


@router.get(
    "/api/v1/endpoints",
    name="ops.api_endpoints",
    response_model=dtos.Endpoints,
)
def api_endpoints():
    """Every page and API endpoint as JSON: methods, parameters, body and role (#230)."""
    return deps.Json({"endpoints": apidocs.endpoints()})


@spec_router.get(OPENAPI_URL)
def openapi(request: Request) -> JSONResponse:
    app: FastAPI = request.app
    return JSONResponse(app.openapi())


@spec_router.get(DOCS_URL)
def docs() -> Response:
    """The API page: web's page with Swagger UI bundled (the current app's, #483), under
    the strict CSP. FastAPI's own loads scripts from a CDN, which the CSP blocks."""
    return frontend.shell()
