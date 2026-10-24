"""The map, the alert board, the API page (Swagger UI), the documentation and
the static files (#424, #433; on FastAPI since #436).  The other pages live
with their area's API (status, availability, circuits, maintenance)."""

import pathlib
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request

from nautobot_maps import alerts, availability, guide, maintenance, navigation, settings, templating
from nautobot_maps.api import deps, files, frontend, pages
from nautobot_maps.api import maintenance as api_maintenance
from nautobot_maps.api.answers import common
from nautobot_maps.api.answers import help as help_answers
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
    menu, the same as the server-rendered pages show, the Maintenance page's
    and Availability's limits, and the map's tiles and first view."""
    at = availability.now()
    view = settings.MAP_DEFAULT_VIEW
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
            "availability": {
                "today": f"{at:%Y-%m-%d}",
                "oldest_day": availability.oldest_day(at),
                "basis": settings.AVAILABILITY_BASIS,
                "target": availability.TARGET_PCT,
            },
            "map": {
                "tile_url": settings.MAP_TILE_URL,
                "tile_attribution": settings.MAP_TILE_ATTRIBUTION,
                "view": {"latitude": view[0], "longitude": view[1], "zoom": view[2]} if view else None,
            },
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/", name="pages.index", include_in_schema=False)
def index():
    """The map: Nautobot locations, address and GPS search, the sites in
    alarm.  Built with Nuxt (#463); the page reads ``/api/page-config``,
    ``/api/locations`` and ``/api/location-alerts``."""
    return frontend.shell()


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
    """The documentation: the overview, then each guide.

    Built with Nuxt (#463); the page reads ``/api/help/<slug>``.  An unknown
    guide is a 404 here, not a page that says so.
    """
    if guide.page(slug) is None:
        raise HTTPException(status_code=404)
    return frontend.shell()


@router.get(
    "/api/help/{slug}",
    name="help.api_help",
    response_model=help_answers.HelpPage,
    responses={404: {"model": common.Error, "description": "No such guide"}},
)
def api_help(slug: str):
    """One documentation page (#433): its title and HTML, and the list of guides."""
    found = guide.page(slug)
    if found is None:
        return deps.Json({"error": "No such guide"}, status_code=404)
    return deps.Json({**found, "contents": guide.contents()})


@router.get("/help/images/{name:path}", name="help.help_image", include_in_schema=False)
def help_image(request: Request, name: str):
    """The screenshots the documentation shows."""
    return files.send(request, guide.IMAGES, name, max_age=3600)


@router.get("/static/{path:path}", name="static", include_in_schema=False)
def static(request: Request, path: str):
    """The pages' scripts, styles, images and vendored libraries."""
    return files.send(request, STATIC, path)
