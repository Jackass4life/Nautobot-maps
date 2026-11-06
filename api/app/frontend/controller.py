"""web/'s pages (#594): built to static files (``npm run generate``) that this
router serves from the same origin, as the current app serves frontend/.

A page's route answers the shell (``200.html``), whose script then draws it from
the API; its files come from ``/_nuxt/``, named by their content and cached for
a year. ``/_nuxt/`` is public (the status page needs it): it is the app's code
only, every data route keeps its own sign-in check.
"""

import pathlib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response

from app.frontend import files
from app.utils.auth import check_request
from app.utils.config import get_settings

ASSETS_PREFIX = "/_nuxt/"
# Content-named files never change: a browser keeps them a year.
ASSET_MAX_AGE = 365 * 24 * 3600
# Pages this router answers itself; the current app's page routes (app/v1/routes/, #604)
# answer the shell through ``shell()``.
PAGES: list[str] = []

NOT_BUILT = (
    "<!DOCTYPE html><title>Not built</title><p>This page is built with Nuxt and hasn't "
    "been built: run <code>npm ci &amp;&amp; npm run generate</code> in <code>web/</code>.</p>"
)

router = APIRouter(dependencies=[Depends(check_request)], include_in_schema=False)


def build_dir() -> pathlib.Path:
    return pathlib.Path(get_settings().web_build_dir)


def shell(headers: dict | None = None) -> Response:
    """Nuxt's page for any of its routes; always asked again (``no-cache``), so a new
    build shows at once. *headers*: more of them (the status page's no-referrer)."""
    page = build_dir() / "200.html"
    if not page.is_file():
        return HTMLResponse(NOT_BUILT, status_code=503)
    return HTMLResponse(
        page.read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-cache", **(headers or {})},
    )


@router.get(ASSETS_PREFIX + "{path:path}")
def asset(request: Request, path: str) -> Response:
    return files.send(request, build_dir() / "_nuxt", path, max_age=ASSET_MAX_AGE)


def _page() -> Response:
    return shell()


for _path in PAGES:
    router.add_api_route(_path, _page, methods=["GET"], include_in_schema=False)
