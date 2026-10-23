"""The map, the alert board, the API page (Swagger UI), the documentation and
the static files (#424, #433; on FastAPI since #436).  The other pages live
with their area's API (status, availability, circuits, maintenance)."""

import pathlib
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request

from nautobot_maps import alerts, guide, maintenance, navigation, settings, templating
from nautobot_maps.api import deps, files, pages
from nautobot_maps.api import maintenance as api_maintenance
from nautobot_maps.api.answers import pages as answers

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES, tags=["Pages"])

STATIC = pathlib.Path(__file__).resolve().parent.parent.parent / "static"


def scope_query(request: Request) -> str:
    """The page's ?tenant= / ?q= (#364, #367) as a query string, "" without
    them: kept on the links between the map, the board and the wall."""
    params = request.query_params
    return urlencode(
        [("tenant", name) for name in params.getlist("tenant") if name.strip()]
        + [("q", text) for text in params.getlist("q") if text.strip()]
    )


@router.get("/api/page-config", name="pages.api_page_config", response_model=answers.PageConfig)
def api_page_config():
    """What the pages built with Nuxt read to draw themselves (#463): who is
    signed in (the top bar), Nautobot's address (links to its pages), the
    menu, the same as the server-rendered pages show, and the Maintenance
    page's limits."""
    return deps.Json(
        {
            "user": templating.bar_user(),
            "nautobot_url": settings.NAUTOBOT_URL or "",
            "pages": navigation.pages(),
            "maintenance": {
                "recent_days": api_maintenance.MAINTENANCE_PAGE_RECENT_DAYS,
                "history_days": api_maintenance.MAINTENANCE_HISTORY_DEFAULT_DAYS,
                "max_sites": maintenance.MAX_SITES,
            },
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/", name="pages.index", include_in_schema=False)
def index(request: Request):
    """The map: Nautobot locations, address and GPS search, alert banners."""
    return pages.page(
        "index.html",
        nautobot_url=settings.NAUTOBOT_URL,
        tile_url=settings.MAP_TILE_URL,
        tile_attribution=settings.MAP_TILE_ATTRIBUTION,
        view=settings.MAP_DEFAULT_VIEW,
        scope_query=scope_query(request),
    )


@router.get("/alerts", name="pages.alert_board", include_in_schema=False)
def alert_board(request: Request):
    """The alert board: every site's severity, down devices, cases and history.

    ``?view=wall`` is the wall-screen view (#243): alarms only, down devices
    listed, no controls.
    """
    return pages.page(
        "alerts.html",
        nautobot_url=settings.NAUTOBOT_URL,
        case_url_template=settings.CASE_URL_TEMPLATE,
        tier_definitions=alerts.alert_level_definitions(),
        wall_view=request.query_params.get("view") == "wall",
        scope_query=scope_query(request),
    )


@router.get("/docs", name="pages.api_docs", include_in_schema=False)
def api_docs():
    """The API (#230, #436): every endpoint in Swagger UI, with Try it, from /openapi.json."""
    return pages.page("docs.html")


@router.get("/openapi.json", name="ops.openapi", include_in_schema=False)
def openapi(request: Request):
    """The API as OpenAPI 3 (#436): what /docs shows, for tools and clients."""
    return deps.Json(request.app.openapi())


@router.get("/help", name="help.help_page", include_in_schema=False)
@router.get("/help/{slug}", name="help.help_page", include_in_schema=False)
def help_page(slug: str = "overview"):
    """The documentation: the overview, then each guide."""
    found = guide.page(slug)
    if found is None:
        raise HTTPException(status_code=404)
    return pages.page("help.html", page=found, contents=guide.contents())


@router.get("/help/images/{name:path}", name="help.help_image", include_in_schema=False)
def help_image(request: Request, name: str):
    """The screenshots the documentation shows."""
    return files.send(request, guide.IMAGES, name, max_age=3600)


@router.get("/static/{path:path}", name="static", include_in_schema=False)
def static(request: Request, path: str):
    """The pages' scripts, styles, images and vendored libraries."""
    return files.send(request, STATIC, path)
