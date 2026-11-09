from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import docs, health, v1
from app.clients import caching, nautobot
from app.frontend import controller as frontend
from app.frontend.middleware import FinishMiddleware
from app.services import scheduler
from app.session import DbSessionMiddleware
from app.utils import access_log
from app.utils.config import get_settings
from app.utils.errors import install_error_handlers
from app.v1 import deps as v1_deps
from app.v1 import legacy_paths
from app.v1 import routes as v1_routes


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Startup (#596, #600): the response cache from the settings, Nautobot's SSL
    warnings, this process's background scheduler (off with BACKGROUND_SYNC_ENABLED=false
    or without Nautobot)."""
    caching.init()
    nautobot.configure_ssl_warnings()
    scheduler.start()
    yield
    scheduler.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        lifespan=lifespan,
        title="Nautobot Maps",
        version="0.3.0",
        # Our own routes (app/docs.py), behind the sign-in rules like every other one.
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.add_middleware(DbSessionMiddleware)
    # Only for other sites' pages: the app's own share its origin (#594), and without
    # any, the middleware would still add "Vary: Origin" to every answer.
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            # A sign-in proxy's cookie may come along (#582); never with "*".
            allow_credentials="*" not in settings.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    # The current app's /api/<path>: 308 to /api/v1/<path> (#613).
    app.add_middleware(legacy_paths.LegacyApiRedirect, routes=lambda: app.routes)
    # The security headers and gzip on every answer, errors included (#594).
    app.add_middleware(FinishMiddleware)
    # HEAD as GET without the body, as the current app (#604).
    app.add_middleware(v1_deps.HeadAsGet)
    # Outermost: one access log line per request, whatever answered it (#613).
    app.add_middleware(access_log.AccessLog)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(docs.router)
    app.include_router(v1.router)
    # The current app's routes (#604): its API under /api/v1, its pages.
    v1_routes.install(app)
    app.openapi = lambda: v1_deps.openapi(app)  # type: ignore[method-assign]
    # web/'s pages and their files (#594).
    app.include_router(frontend.router)
    return app


app = create_app()
