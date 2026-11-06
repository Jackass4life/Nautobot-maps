from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import docs, health, v1
from app.frontend import controller as frontend
from app.frontend.middleware import FinishMiddleware
from app.session import DbSessionMiddleware
from app.utils.config import get_settings
from app.utils.errors import install_error_handlers


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Nautobot Maps",
        version="0.1.0",
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
    # Outermost: the security headers and gzip on every answer, errors included (#594).
    app.add_middleware(FinishMiddleware)
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(docs.router)
    app.include_router(v1.router)
    # web/'s pages and their files (#594).
    app.include_router(frontend.router)
    return app


app = create_app()
