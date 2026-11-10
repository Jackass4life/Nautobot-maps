"""The app: its middleware, error handlers and routes. ``app.api:app`` is what
gunicorn (docker-entrypoint.sh) and the development server (run.py) run."""

from fastapi import FastAPI

from app import v1
from app.core import cors
from app.core.exceptions import install_error_handlers
from app.core.settings import get_settings
from app.core.version import version
from app.db.middleware import DbSessionMiddleware
from app.frontend import controller as frontend
from app.frontend.middleware import FinishMiddleware
from app.lifespan import lifespan
from app.logs import middleware as access_log
from app.v1 import deps as v1_deps
from app.v1 import legacy_paths
from app.v1 import sections as v1_sections


def create_app() -> FastAPI:
    app = FastAPI(
        lifespan=lifespan,
        title="Nautobot Maps",
        version=version(),
        # One tag per page section, in the menu's order, with what each is for (#623).
        openapi_tags=v1_sections.openapi_tags(),
        # Our own routes (app/v1/documentation/), behind the sign-in rules like every other one.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(DbSessionMiddleware)
    cors.configure(app, get_settings())
    # The current app's /api/<path>: 308 to /api/v1/<path> (#613).
    app.add_middleware(legacy_paths.LegacyApiRedirect, routes=lambda: app.routes)
    # The security headers and gzip on every answer, errors included (#594).
    app.add_middleware(FinishMiddleware)
    # HEAD as GET without the body, as the current app (#604).
    app.add_middleware(v1_deps.HeadAsGet)
    # Outermost: one access log line per request, whatever answered it (#613).
    app.add_middleware(access_log.AccessLog)
    install_error_handlers(app)
    # Every section's routes (#624): the API under /api/v1, the pages, /healthz.
    v1.install(app)
    app.openapi = lambda: v1_deps.openapi(app)  # type: ignore[method-assign]
    # web/'s pages and their files (#594).
    app.include_router(frontend.router)
    return app


app = create_app()
