from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import health, v1
from app.session import DbSessionMiddleware
from app.utils.config import get_settings
from app.utils.errors import install_error_handlers


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Example API",
        version="0.1.0",
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.add_middleware(DbSessionMiddleware)
    # Added last = outermost, so CORS headers are set on every response.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        # The sign-in proxy's cookie comes along from web/ (#582); never with "*".
        allow_credentials="*" not in settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(v1.router)
    return app


app = create_app()
