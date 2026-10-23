"""The pages built with Nuxt (#463): ``frontend/``, built to static files
(``npm run generate``) that this router serves from the same origin.

A migrated page's route answers ``shell()``, Nuxt's page, whose script then
draws it from the API; its files come from ``/_nuxt/``, named by their content
and cached for a year.  ``/_nuxt/`` is public (the public status page needs
it): it is the app's code only, every data route keeps its sign-in check.
"""

import pathlib

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from nautobot_maps.api import deps, files

BUILD = pathlib.Path(__file__).resolve().parent.parent.parent / "frontend" / ".output" / "public"
ASSETS_PREFIX = "/_nuxt/"
# Content-named files never change: a browser keeps them a year.
ASSET_MAX_AGE = 365 * 24 * 3600

NOT_BUILT = (
    "<!DOCTYPE html><title>Not built</title><p>This page is built with Nuxt and hasn't been built: "
    "run <code>npm ci &amp;&amp; npm run generate</code> in <code>frontend/</code>.</p>"
)

router = APIRouter(route_class=deps.AppRoute, dependencies=deps.ROUTER_DEPENDENCIES)


def shell(headers: dict | None = None) -> HTMLResponse:
    """Nuxt's page for any of its routes; always asked again (``no-cache``),
    so a new build shows at once."""
    page = BUILD / "200.html"
    if not page.is_file():
        return HTMLResponse(NOT_BUILT, status_code=503)
    return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache", **(headers or {})})


@router.get(ASSETS_PREFIX + "{path:path}", name="frontend.asset", include_in_schema=False)
def asset(request: Request, path: str):
    return files.send(request, BUILD / "_nuxt", path, max_age=ASSET_MAX_AGE)
